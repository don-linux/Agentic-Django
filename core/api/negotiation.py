"""
HTTP pieces Ninja does not provide natively, for every route under /api/v1/:
the 406 and 405 JSON bodies, HEAD and OPTIONS.

The schema and the docs are Ninja's own views (core/api/api.py). The docs page
stays HTML; Accept: application/json on /docs/ is 406 even for an anonymous
client (tests/http/test_openapi.py).
"""

from __future__ import annotations

import functools
import json
import re
from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Any

from django.http import HttpRequest
from django.http import HttpResponse
from django.http import HttpResponseNotAllowed
from django.http import JsonResponse
from django.urls import Resolver404
from django.urls import get_script_prefix
from django.urls import resolve

from core.api.api import api
from core.errors import error_body

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Iterable

JSON = "application/json"

# Allow lists the route's operations in the order of Django's
# View.http_method_names, then HEAD (served by the GET operation) and OPTIONS
# (answered here). The methods come from the
# project API's OpenAPI document, so a new router needs no entry here.
_METHOD_ORDER = ("GET", "POST", "PUT", "PATCH", "DELETE")
# The URL names of Ninja's schema and docs views (ninja/openapi/urls.py).
_DOCS_ROUTES = {"openapi-json", "openapi-view"}
# What Ninja's router answers a method a path lacks (ninja/operation.py,
# PathView._not_allowed). See ADR 0017.
METHOD_NOT_ALLOWED_DETAIL = "Method not allowed"
_ROUTE_PARAM = re.compile(r"<(?:[^>:]+:)?([^>]+)>")


def _api_path(request: HttpRequest) -> str | None:
    # path_info, as URL resolution: under a script prefix (SCRIPT_NAME, or
    # FORCE_SCRIPT_NAME behind a proxy) request.path starts with the prefix.
    path = request.path_info
    prefix = "/api/v1/"
    if not path.startswith(prefix):
        return None
    return path[len(prefix) :]


def _allow_header(methods: Iterable[str]) -> str:
    wanted = {m.upper() for m in methods}
    ordered = [m for m in _METHOD_ORDER if m in wanted]
    if "GET" in wanted:
        ordered.append("HEAD")
    ordered.append("OPTIONS")
    return ", ".join(ordered)


@functools.cache
def _operations_by_route() -> dict[str, dict[str, Any]]:
    """OpenAPI path ("/api/v1/users/{id}/") -> {method: operation}.

    Built once per process, on the first request. Under a script prefix
    (SCRIPT_NAME, or FORCE_SCRIPT_NAME behind a proxy) WSGIHandler sets it for
    that request, and Ninja's paths start with it, since they come from
    reverse(). The keys are routes as URL resolution sees them, without the
    prefix, so the prefix is taken off the API root first.
    """
    root = api.get_root_path({}).removeprefix(get_script_prefix().rstrip("/"))
    paths = api.get_openapi_schema(path_prefix=root).get("paths", {})
    return {
        path: {m.upper(): op for m, op in item.items() if m.upper() in _METHOD_ORDER}
        for path, item in paths.items()
    }


def _route_operations(request: HttpRequest) -> dict[str, Any] | None:
    """The operations of the API route this request resolves to, else None.

    A path that resolves to nothing is Django's 404, not a 405: Resolver404
    happens before any method check, as in Django's URL resolution.
    """
    try:
        match = resolve(request.path_info)
    except Resolver404:
        return None
    if match.url_name in _DOCS_ROUTES:
        return {"GET": {}}
    route = "/" + _ROUTE_PARAM.sub(r"{\1}", match.route)
    return _operations_by_route().get(route)


# RFC 9110 section 12.4.2: qvalue = ( "0" [ "." 0*3DIGIT ] ) / ...
_QVALUE_DECIMALS = 3


def _is_zero_weight(qvalue: str) -> bool:
    """A qvalue of 0 ("0", "0.", "0.0" ... "0.000"): "not acceptable"."""
    whole, _, decimals = qvalue.partition(".")
    return whole == "0" and len(decimals) <= _QVALUE_DECIMALS and set(decimals) <= {"0"}


def _media_ranges(accept: str) -> list[tuple[str, str, dict[str, str]]]:
    """The Accept header's media ranges as (type, subtype, parameters), lowercased.

    RFC 9110 section 12.5.1: a media range is */*, type/* or type/subtype, so
    "*/json" is none and matches nothing. A range with q=0 is kept: it is "not
    acceptable" (section 12.4.2) for the media types it gives the weight to
    (_is_acceptable). A present but empty Accept has no range: it is a 406. The
    header's size is bounded by the server (gunicorn limit_request_field_size).
    """
    ranges: list[tuple[str, str, dict[str, str]]] = []
    for member in filter(None, (part.strip() for part in accept.split(","))):
        media_type, *pairs = member.split(";")
        main, _, sub = media_type.strip().lower().partition("/")
        if main == "*" and sub != "*":
            continue
        params: dict[str, str] = {}
        for pair in pairs:
            key, _, value = pair.partition("=")
            if key.strip():
                params[key.strip().lower()] = value.strip().strip('"')
        ranges.append((main, sub, params))
    return ranges


def _precedence(main: str, sub: str, params: dict[str, str]) -> int:
    """How specific a media range is (RFC 9110 section 12.5.1): */* 0, type/* 1,
    type/subtype 2, with a parameter other than q 3."""
    if main == "*":
        return 0
    if sub == "*":
        return 1
    return 2 if set(params) <= {"q"} else 3


def _covers(r_main: str, r_sub: str, main: str, sub: str) -> bool:
    """Whether the media range r_main/r_sub matches the media type main/sub,
    which has no parameters."""
    return r_main in {"*", main} and r_sub in {"*", sub}


def _refuses_only_its_variant(params: dict[str, str]) -> bool:
    """Whether a range is q=0 with parameters other than q. RFC 9110 section
    12.5.1, table 5: such a range applies to the variant with those parameters,
    so it refuses only that variant, as Django's MediaType.match; the media
    types offered have no parameters, so it covers none of them."""
    return bool(set(params) - {"q"}) and _is_zero_weight(params.get("q", ""))


def _is_acceptable(
    media_type: str, ranges: list[tuple[str, str, dict[str, str]]]
) -> bool:
    """RFC 9110 section 12.5.1: the most specific ranges that cover a media
    type give its weight (type/subtype with parameters, type/subtype, type/*,
    then */*, as _precedence). It is acceptable unless all of them have q=0;
    a media type no range covers is not acceptable. A q=0 range with
    parameters covers none (_refuses_only_its_variant); above 0 it does. Of
    two equally specific ranges, one above 0 and one with q=0, the media type
    is acceptable: RFC 9110 does not define that case."""
    main, _, sub = media_type.partition("/")
    covering = [
        r
        for r in ranges
        if _covers(r[0], r[1], main, sub) and not _refuses_only_its_variant(r[2])
    ]
    if not covering:
        return False
    top = max(_precedence(*r) for r in covering)
    return any(
        not _is_zero_weight(params.get("q", ""))
        for r_main, r_sub, params in covering
        if _precedence(r_main, r_sub, params) == top
    )


def _produces(subpath: str) -> str:
    """The one media type a route renders: the docs page is HTML, the rest JSON."""
    return "text/html" if subpath == "docs/" else JSON


def _acceptable(request: HttpRequest, subpath: str) -> bool:
    """Whether the request's Accept takes what the route renders. No Accept is
    */*; a present but empty one offers nothing."""
    accept = request.headers.get("Accept")
    ranges = _media_ranges("*/*" if accept is None else accept)
    return _is_acceptable(_produces(subpath), ranges)


_COMPACT: dict[str, Any] = {"separators": (",", ":"), "ensure_ascii": False}


def _error(code: str, detail: str, status: int, **headers: str) -> JsonResponse:
    response = JsonResponse(
        error_body(code, detail), status=status, json_dumps_params=_COMPACT
    )
    for key, value in headers.items():
        response[key] = value
    return response


def _not_acceptable() -> JsonResponse:
    return _error(
        "not_acceptable",
        "Could not satisfy the request Accept header.",
        HTTPStatus.NOT_ACCEPTABLE,
    )


class ApiContractMiddleware:
    """406 / 415 / 405 {code, detail} on /api/v1/ before and after Ninja."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        subpath = _api_path(request)
        if subpath is None:
            return self.get_response(request)

        operations = _route_operations(request)
        allow = _allow_header(operations) if operations is not None else None
        early = self._before(request, subpath, allow)
        # 415 is decided by core.api.parsers, when Ninja reads the body.
        # Every API route is in the schema (test_method_negotiation pins it), so
        # a method the route lacks never reaches Ninja: _before answered 405.
        # Allow goes on the 405 and on OPTIONS only, as in Django and Ninja (RFC
        # 9110, section 10.2.1, requires it on a 405).
        return early if early is not None else self._dispatch(request)

    def _before(
        self, request: HttpRequest, subpath: str, allow: str | None
    ) -> HttpResponse | None:
        """405, OPTIONS and 406 answered before the operation runs.

        In the order Django and Ninja dispatch: a path that matches no route is
        Django's 404 whatever the Accept, then a method the route lacks is 405,
        and OPTIONS answers without negotiating, as it has no body. Only then
        is an Accept the route cannot produce a 406, before the staff and
        authentication checks, even when anonymous.
        """
        method = request.method or "GET"
        if allow is None:
            return None
        if method not in {m.strip() for m in allow.split(",")}:
            return self._not_allowed(method, allow)
        if method == "OPTIONS":
            return self._options(allow)
        if not _acceptable(request, subpath):
            return _not_acceptable()
        return None

    def _dispatch(self, request: HttpRequest) -> HttpResponse:
        # HEAD runs the GET operation (Django's View does the same); the server
        # drops the body. Operations declare GET only, so the schema has no
        # duplicate HEAD operationIds (OpenAPI 3.1 requires unique ones).
        if request.method != "HEAD":
            return self.get_response(request)
        request.method = "GET"
        try:
            return self.get_response(request)
        finally:
            request.method = "HEAD"

    def _options(self, allow: str) -> HttpResponse:
        """OPTIONS as Django's View.options() answers it: 200, Allow, no body.

        No authentication, as in Django: it only says which methods the route
        has (RFC 9110, section 9.3.7). A CORS preflight never gets here:
        django-cors-headers answers it first.
        """
        response = HttpResponse()
        response["Allow"] = allow
        response["Content-Length"] = "0"
        return response

    def _not_allowed(self, method: str, allow: str) -> HttpResponse:
        body = json.dumps(
            error_body("method_not_allowed", METHOD_NOT_ALLOWED_DETAIL),
            separators=(",", ":"),
            ensure_ascii=False,
        )
        # HttpResponseNotAllowed joins the list in order: Allow keeps that order.
        return HttpResponseNotAllowed(
            [m.strip() for m in allow.split(",")],
            content=body.encode(),
            content_type="application/json",
        )
