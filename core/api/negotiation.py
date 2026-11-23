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
    """OpenAPI path ("/api/v1/users/{id}/") -> {method: operation}."""
    paths = api.get_openapi_schema().get("paths", {})
    return {
        path: {m.upper(): op for m, op in item.items() if m.upper() in _METHOD_ORDER}
        for path, item in paths.items()
    }


def _route_operations(request: HttpRequest) -> dict[str, Any] | None:
    """The operations of the API route this request resolves to, else None.

    A path that resolves to nothing is Django's 404, not a 405: Resolver404
    happens before any method check, as in the DRF contract.
    """
    try:
        match = resolve(request.path_info)
    except Resolver404:
        return None
    if match.url_name in _DOCS_ROUTES:
        return {"GET": {}}
    route = "/" + _ROUTE_PARAM.sub(r"{\1}", match.route)
    return _operations_by_route().get(route)


# DRF 3.18.3 DefaultContentNegotiation's bounds on the Accept header
# (rest_framework/negotiation.py: max_accept_header_length, max_accept_tokens
# and max_media_type_length).
_MAX_ACCEPT_HEADER_LENGTH = 8192
_MAX_ACCEPT_TOKENS = 64
_MAX_MEDIA_TYPE_LENGTH = 256


def _accept_members(accept: str) -> list[str]:
    """DRF's get_accept_list: the Accept members it reads, at most 64.

    A header over 8192 characters is cut after its last comma before that
    length (to nothing without one), and empty members and members over 256
    characters are skipped, not counted.
    """
    if len(accept) > _MAX_ACCEPT_HEADER_LENGTH:
        accept = accept[: accept.rfind(",", 0, _MAX_ACCEPT_HEADER_LENGTH) + 1]
    members = [member.strip() for member in accept.split(",")]
    kept = [m for m in members if m and len(m) <= _MAX_MEDIA_TYPE_LENGTH]
    return kept[:_MAX_ACCEPT_TOKENS]


# RFC 9110 section 12.4.2: qvalue = ( "0" [ "." 0*3DIGIT ] ) / ...
_QVALUE_DECIMALS = 3


def _is_zero_weight(qvalue: str) -> bool:
    """A qvalue of 0 ("0", "0.", "0.0" ... "0.000"): "not acceptable"."""
    whole, _, decimals = qvalue.partition(".")
    return whole == "0" and len(decimals) <= _QVALUE_DECIMALS and set(decimals) <= {"0"}


def _media_ranges(accept: str) -> list[tuple[str, str, dict[str, str]]]:
    """The Accept header's media ranges as (type, subtype, parameters), lowercased.

    RFC 9110 section 12.5.1: a media range is */*, type/* or type/subtype, so
    "*/json" is none and matches nothing (DRF 3.18.3 matched it). A range with
    q=0 is kept: it is "not acceptable" (section 12.4.2) for the media types it
    gives the weight to (_is_acceptable). A present but empty Accept has no
    range: it is a 406.
    """
    ranges: list[tuple[str, str, dict[str, str]]] = []
    for member in _accept_members(accept):
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
    """DRF's _MediaType.precedence: */* 0, type/* 1, type/subtype 2, with a
    parameter other than q 3."""
    if main == "*":
        return 0
    if sub == "*":
        return 1
    return 2 if set(params) <= {"q"} else 3


def _covers(r_main: str, r_sub: str, main: str, sub: str) -> bool:
    """Whether the media range r_main/r_sub matches main/sub (as DRF's
    _MediaType.match for a media type without parameters)."""
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
    parameters covers none (_refuses_only_its_variant); above 0 it does."""
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


def _negotiate(
    accept: str | None, offered: tuple[str, ...]
) -> tuple[str, dict[str, str]] | None:
    """DRF's DefaultContentNegotiation.select_renderer over `offered`, in order,
    after RFC 9110's weights.

    Only the acceptable media types count (_is_acceptable). The most specific
    media ranges above q=0 go first; among ranges of one precedence the first
    acceptable media type that one of them matches wins. q values other than 0
    are ignored, as in DRF. A request without Accept is */*. Returns the media
    type and the parameters of the range it matched, or None (406).
    """
    ranges = _media_ranges("*/*" if accept is None else accept)
    acceptable = [m for m in offered if _is_acceptable(m, ranges)]
    for level in (3, 2, 1, 0):
        same = [
            r
            for r in ranges
            if _precedence(*r) == level and not _is_zero_weight(r[2].get("q", ""))
        ]
        for media_type in acceptable:
            main, _, sub = media_type.partition("/")
            for r_main, r_sub, params in same:
                if _covers(r_main, r_sub, main, sub):
                    return media_type, params
    return None


def _produces(subpath: str) -> tuple[str, ...]:
    """What the route renders: the docs page is HTML, everything else JSON."""
    return {"docs/": ("text/html",)}.get(subpath, (JSON,))


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
        early = self._before(request, subpath, operations, allow)
        # 415 is decided by core.api.parsers, when Ninja reads the body.
        # Every API route is in the schema (test_method_negotiation pins it), so
        # a method the route lacks never reaches Ninja: _before answered 405.
        # Allow goes on the 405 and on OPTIONS only, as in Django and Ninja (RFC
        # 9110, section 10.2.1, requires it on a 405).
        return early if early is not None else self._dispatch(request)

    def _before(
        self,
        request: HttpRequest,
        subpath: str,
        operations: dict[str, Any] | None,
        allow: str | None,
    ) -> HttpResponse | None:
        """406, 405 and OPTIONS answered before the operation runs."""
        method = request.method or "GET"
        # Content negotiation comes first, as in DRF's APIView.initial: an Accept
        # the route cannot produce is 406 before the 405, OPTIONS and the staff
        # and auth checks, even when anonymous. A path that matches no route is
        # Django's 404 whatever the Accept.
        if operations is not None and not _negotiate(
            request.headers.get("Accept"), _produces(subpath)
        ):
            return _not_acceptable()
        if allow is not None and method not in {m.strip() for m in allow.split(",")}:
            return self._not_allowed(method, allow)
        if method == "OPTIONS" and allow is not None:
            return self._options(allow)
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
