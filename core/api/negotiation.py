"""
HTTP pieces Ninja does not provide natively, for every route under /api/v1/:
the 406 and 405 JSON bodies, HEAD and OPTIONS.

The schema and the docs are Ninja's own views (core/api/api.py). The docs page
is HTML: Accept: application/json on /docs/ is 406 even for an anonymous
client (apps/users/tests/http/test_openapi.py).
"""

from __future__ import annotations

import functools
import re
from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Any

from django.http import HttpRequest
from django.http import HttpResponse
from django.http import HttpResponseBase
from django.http import JsonResponse
from django.urls import Resolver404
from django.urls import get_script_prefix
from django.urls import resolve

from core.api.api import api
from core.errors import METHOD_NOT_ALLOWED_CODE
from core.errors import NOT_ACCEPTABLE_CODE
from core.errors import json_error

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Iterable

JSON = "application/json"

# Allow lists the route's operations in the order of Django's
# View.http_method_names, then HEAD (served by the GET operation) and OPTIONS
# (answered here). The methods come from the
# project API's OpenAPI document, so a new router needs no entry here.
_METHOD_ORDER = ("GET", "POST", "PUT", "PATCH", "DELETE")
# Ninja's schema and docs views (ninja/openapi/urls.py), by URL name: they are
# not in the OpenAPI document, so their one operation is spelled out here.
_DOCS_ROUTES: dict[str, dict[str, Any]] = {
    "openapi-json": {"GET": {"responses": {"200": {"content": {JSON: {}}}}}},
    "openapi-view": {"GET": {"responses": {"200": {"content": {"text/html": {}}}}}},
}
# What Ninja's router answers a method a path lacks (ninja/operation.py,
# PathView._not_allowed). See ADR 0017.
METHOD_NOT_ALLOWED_DETAIL = "Method not allowed"
_ROUTE_PARAM = re.compile(r"<(?:[^>:]+:)?([^>]+)>")


def _is_api_path(request: HttpRequest) -> bool:
    # path_info, as URL resolution: under a script prefix (SCRIPT_NAME, or
    # FORCE_SCRIPT_NAME behind a proxy) request.path starts with the prefix.
    return request.path_info.startswith("/api/v1/")


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
        return _DOCS_ROUTES[match.url_name]
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


def _produces(operations: dict[str, Any], method: str) -> list[str]:
    """The media types an operation answers with on success.

    They are the content of its 2xx responses in the OpenAPI document: JSON for
    every operation the template ships, HTML for the docs page. HEAD is the GET
    operation.
    """
    operation = operations.get("GET" if method == "HEAD" else method, {})
    return [
        media_type.split(";", 1)[0].strip()
        for status, response in operation.get("responses", {}).items()
        if str(status).startswith("2")
        for media_type in response.get("content", {})
    ]


def _acceptable(request: HttpRequest, produces: list[str]) -> bool:
    """Whether the request's Accept takes one of the media types produced.

    No Accept is */*; a present but empty one offers nothing. An operation that
    declares no content on success (a 204, or a download whose media type the
    schema leaves out) has nothing to negotiate.
    """
    if not produces:
        return True
    accept = request.headers.get("Accept")
    ranges = _media_ranges("*/*" if accept is None else accept)
    return any(_is_acceptable(media_type, ranges) for media_type in produces)


def _not_acceptable() -> JsonResponse:
    return json_error(
        NOT_ACCEPTABLE_CODE,
        "Could not satisfy the request Accept header.",
        HTTPStatus.NOT_ACCEPTABLE,
    )


class ApiContractMiddleware:
    """The 405, OPTIONS, 406 and HEAD of every route under /api/v1/, before Ninja.

    415 is not here: core.api.parsers decides it when Ninja reads the body.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        if not _is_api_path(request):
            return self.get_response(request)

        operations = _route_operations(request)
        early = self._before(request, operations)
        # Every API route is in the schema (test_method_negotiation pins it), so
        # a method the route lacks never reaches Ninja: _before answered 405.
        # Allow goes on the 405 and on OPTIONS only, as in Django and Ninja (RFC
        # 9110, section 10.2.1, requires it on a 405).
        return early if early is not None else self._dispatch(request)

    def _before(
        self, request: HttpRequest, operations: dict[str, Any] | None
    ) -> HttpResponse | None:
        """405, OPTIONS and 406 answered before the operation runs.

        In the order Django and Ninja dispatch: a path that matches no route is
        Django's 404 whatever the Accept, then a method the route lacks is 405,
        and OPTIONS answers without negotiating, as it has no body. Only then
        is an Accept the operation cannot produce a 406, before the staff and
        authentication checks, even when anonymous.
        """
        method = request.method or "GET"
        if operations is None:
            return None
        allow = _allow_header(operations)
        if method not in {m.strip() for m in allow.split(",")}:
            return self._not_allowed(method, allow)
        if method == "OPTIONS":
            return self._options(allow)
        if not _acceptable(request, _produces(operations, method)):
            return _not_acceptable()
        return None

    def _dispatch(self, request: HttpRequest) -> HttpResponseBase:
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
        response = json_error(
            METHOD_NOT_ALLOWED_CODE,
            METHOD_NOT_ALLOWED_DETAIL,
            HTTPStatus.METHOD_NOT_ALLOWED,
        )
        response["Allow"] = allow
        return response
