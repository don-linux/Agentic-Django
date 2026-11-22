"""
HTTP contract pieces Ninja does not provide natively: 406, 415, 405 bodies,
staff-only schema/docs, and YAML-by-default schema.

The docs page stays HTML; Accept: application/json on /docs/ is 406 even
for an anonymous client (tests/http/test_openapi.py).
"""

from __future__ import annotations

import functools
import json
import re
from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Any

import yaml
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import HttpResponseNotAllowed
from django.http import JsonResponse
from django.urls import Resolver404
from django.urls import resolve

from core.api.api import api
from core.api.auth import API_AUTH
from core.api.auth import x_session_token_auth
from core.errors import error_body

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Iterable

JSON = "application/json"
SCHEMA_YAML = "application/vnd.oai.openapi"
SCHEMA_JSON = "application/vnd.oai.openapi+json"
# The schema's media types: SpectacularAPIView's four renderers, in their order.
SCHEMA_MEDIA_TYPES = (SCHEMA_YAML, "application/yaml", SCHEMA_JSON, JSON)

# Allow lists the route's operations in DRF's order, then HEAD (served by the
# GET operation) and OPTIONS (answered here). The methods come from the
# project API's OpenAPI document, so a new router needs no entry here.
_METHOD_ORDER = ("GET", "POST", "PUT", "PATCH", "DELETE")
_DOCS_ROUTES = {"api-schema", "api-docs"}
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
        return {"GET": {"summary": match.url_name}}
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
    """What the route renders: the docs HTML, the schema's four types, else JSON."""
    return {"docs/": ("text/html",), "schema/": SCHEMA_MEDIA_TYPES}.get(
        subpath, (JSON,)
    )


_COMPACT: dict[str, Any] = {"separators": (",", ":"), "ensure_ascii": False}


def _error(code: str, detail: str, status: int, **headers: str) -> JsonResponse:
    response = JsonResponse(
        error_body(code, detail), status=status, json_dumps_params=_COMPACT
    )
    for key, value in headers.items():
        response[key] = value
    return response


def staff_docs_required(
    view: Callable[..., HttpResponse],
) -> Callable[..., HttpResponse]:
    """
    Staff-only docs/schema, with the same JSON 401/403 as the rest of the API.

    staff_member_required would redirect to the admin login. Session or
    X-Session-Token is accepted. The token goes through the API's public
    allauth auth (allauth.headless.contrib.ninja.security.XSessionTokenAuth,
    core/api/auth.py), which sets request.user, as on every other route.
    """

    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        user = (
            request.user
            if getattr(request, "user", None) is not None
            and request.user.is_authenticated
            else None
        )
        if user is None:
            user = x_session_token_auth(request)
        if user is None:
            response = _error(
                "not_authenticated",
                "Authentication credentials were not provided.",
                HTTPStatus.UNAUTHORIZED,
            )
            response["WWW-Authenticate"] = "X-Session-Token"
            return response
        if not getattr(user, "is_staff", False):
            return _error(
                "permission_denied",
                "You do not have permission to perform this action.",
                HTTPStatus.FORBIDDEN,
            )
        return view(request, *args, **kwargs)

    return wrapped


def _not_acceptable() -> JsonResponse:
    return _error(
        "not_acceptable",
        "Could not satisfy the request Accept header.",
        HTTPStatus.NOT_ACCEPTABLE,
    )


def _options_name(operations: dict[str, Any]) -> str:
    first: dict[str, Any] = next(iter(operations.values()), {})
    operation: dict[str, Any] = operations.get("GET") or first
    return str(operation.get("summary") or "")


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
        # 415 is decided by core.api.parsers, after the path lookup validator.
        # Every API route is in the schema (test_method_negotiation pins it), so
        # a method the route lacks never reaches Ninja: _before answered 405.
        response = early if early is not None else self._dispatch(request)
        # DRF sent Allow on every response of a resource, the early 406 included
        # (APIView.finalize_response sets it after the handler or the exception).
        if allow is not None and not response.has_header("Allow"):
            response["Allow"] = allow
        return response

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
        if method == "OPTIONS" and operations is not None:
            if subpath in {"docs/", "schema/"}:
                # These routes are staff-only even for metadata requests.
                # The early OPTIONS response must not bypass their view guard.
                # The schema answers as SpectacularAPIView did: its own metadata
                # in the negotiated schema format.
                protected = staff_docs_required(
                    lambda req: (
                        schema_options_view(req)
                        if subpath == "schema/"
                        else self._options(req, operations, allow or "")
                    )
                )
                return protected(request)
            return self._options(request, operations, allow or "")
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

    def _options(
        self, request: HttpRequest, operations: dict[str, Any], allow: str
    ) -> HttpResponse:
        """OPTIONS as DRF answered it: 401 when anonymous, else 200 metadata."""
        if not any(auth(request) for auth in API_AUTH):
            return _error(
                "not_authenticated",
                "Authentication credentials were not provided.",
                HTTPStatus.UNAUTHORIZED,
                **{"WWW-Authenticate": "X-Session-Token", "Allow": allow},
            )
        body = {
            # DRF SimpleMetadata shape; the name is the GET (or first) summary.
            "name": _options_name(operations),
            "description": "",
            "renders": [JSON],
            "parses": [JSON],
        }
        response = JsonResponse(body, json_dumps_params=_COMPACT)
        response["Allow"] = allow
        return response

    def _not_allowed(self, method: str, allow: str) -> HttpResponse:
        body = json.dumps(
            error_body("method_not_allowed", f'Method "{method}" not allowed.'),
            separators=(",", ":"),
            ensure_ascii=False,
        )
        # HttpResponseNotAllowed joins the list in order: Allow keeps DRF's order.
        return HttpResponseNotAllowed(
            [m.strip() for m in allow.split(",")],
            content=body.encode(),
            content_type="application/json",
        )


class _SchemaYamlDumper(yaml.SafeDumper):
    """OpenApiYamlRenderer's dumper: a multi-line string is a | block."""


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    scalar = dumper.represent_str(data)
    if "\n" in data:
        scalar.style = "|"
    return scalar


_SchemaYamlDumper.add_representer(str, _represent_str)


def _json_indent(params: dict[str, str]) -> int:
    """OpenApiJsonRenderer.get_indent: the indent parameter clamped to 0-8; 0 or
    none is 4."""
    try:
        indent = max(min(int(params["indent"]), 8), 0)
    except KeyError, ValueError:
        return 4
    return indent or 4


def render_schema(request: HttpRequest, data: dict[str, Any]) -> HttpResponse:
    """`data` as SpectacularAPIView's four renderers wrote it, chosen by Accept.

    application/vnd.oai.openapi and application/yaml are YAML with a UTF-8
    charset; application/vnd.oai.openapi+json and application/json are DRF's
    JSONRenderer (no charset, non-ASCII as is, U+2028/U+2029 escaped), indented
    by the Accept's indent parameter or 4. ?format= is ignored
    (URL_FORMAT_OVERRIDE is off in the previous contract).
    """
    chosen = _negotiate(request.headers.get("Accept"), SCHEMA_MEDIA_TYPES)
    if chosen is None:
        # ApiContractMiddleware answers this 406 first; the view keeps it alone.
        return _not_acceptable()
    media_type, params = chosen
    # Ninja's schema may contain non-JSON types: str() them, as before.
    if media_type in {SCHEMA_JSON, JSON}:
        text = json.dumps(
            data,
            indent=_json_indent(params),
            ensure_ascii=False,
            allow_nan=False,
            default=str,
        )
        text = text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
        return HttpResponse(text.encode(), content_type=media_type)
    dumped = yaml.dump(
        json.loads(json.dumps(data, default=str)),
        Dumper=_SchemaYamlDumper,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
    return HttpResponse(dumped, content_type=f"{media_type}; charset=utf-8")


def schema_view(request: HttpRequest) -> HttpResponse:
    """OpenAPI document: YAML by default, JSON when Accept asks for it."""
    return render_schema(request, api.get_openapi_schema())


# SpectacularAPIView's OPTIONS (DRF SimpleMetadata) in the previous contract:
# SchemaView's name and docstring, its four renderers and the JSON parser.
SCHEMA_METADATA: dict[str, Any] = {
    "name": "Schema",
    "description": "Staff-only OpenAPI schema; errors stay application/json.",
    "renders": list(SCHEMA_MEDIA_TYPES),
    "parses": [JSON],
}


def schema_options_view(request: HttpRequest) -> HttpResponse:
    """Staff OPTIONS on the schema: its metadata, in the negotiated schema format."""
    return render_schema(request, SCHEMA_METADATA)


def docs_view(request: HttpRequest) -> HttpResponse:
    """Staff Swagger UI without an inline script (CSP). ?script= returns the init JS."""
    if "script" in request.GET:
        js = (
            "/* Swagger UI init (split, no inline script) */\n"
            "window.ui = SwaggerUIBundle({\n"
            "  url: '/api/v1/schema/',\n"
            "  dom_id: '#swagger-ui',\n"
            "  deepLinking: true,\n"
            "  layout: 'BaseLayout'\n"
            "});\n"
        )
        return HttpResponse(js, content_type="application/javascript")
    html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>Project API</title>
  <link rel="stylesheet" href="/static/ninja/swagger-ui.css"/>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="/static/ninja/swagger-ui-bundle.js"></script>
  <script src="/api/v1/docs/?script="></script>
</body>
</html>
"""
    return HttpResponse(html, content_type="text/html; charset=utf-8")
