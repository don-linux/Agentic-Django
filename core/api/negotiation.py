"""
HTTP contract pieces Ninja does not provide natively: 406, 415, 405 bodies,
staff-only schema/docs, and YAML-by-default schema.

The docs page stays HTML; Accept: application/json on /docs/ is 406 even
for an anonymous client (tests/http/test_openapi.py).
"""

from __future__ import annotations

import json
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
from core.api.auth import x_session_token_auth
from core.errors import error_body

if TYPE_CHECKING:
    from collections.abc import Callable

JSON = "application/json"
SCHEMA_YAML = "application/vnd.oai.openapi"
SCHEMA_JSON = "application/vnd.oai.openapi+json"
# The schema's media types: SpectacularAPIView's four renderers, in their order.
SCHEMA_MEDIA_TYPES = (SCHEMA_YAML, "application/yaml", SCHEMA_JSON, JSON)

# Path prefixes under /api/v1/ and the methods the HTTP contract advertises.
# HEAD and OPTIONS are advertised even when Ninja only registered GET/PATCH.
_ALLOW: list[tuple[str, str]] = [
    ("users/me/", "GET, PATCH, HEAD, OPTIONS"),
    # Detail after me so "me" is not parsed as an id.
    ("users/", "GET, HEAD, OPTIONS"),  # list, exact suffix handled below
]


def _api_path(request: HttpRequest) -> str | None:
    # path_info, as URL resolution: under a script prefix (SCRIPT_NAME, or
    # FORCE_SCRIPT_NAME behind a proxy) request.path starts with the prefix.
    path = request.path_info
    prefix = "/api/v1/"
    if not path.startswith(prefix):
        return None
    return path[len(prefix) :]


def _allow_for(subpath: str) -> str | None:
    # Only exact, slash-terminated API paths. A missing trailing slash is a 404,
    # not a 405 (tests/http/test_rest_conventions.py).
    if subpath == "users/":
        return "GET, HEAD, OPTIONS"
    if subpath == "users/me/":
        return "GET, PATCH, HEAD, OPTIONS"
    if (
        subpath.startswith("users/")
        and subpath.endswith("/")
        and subpath != "users/me/"
    ):
        return "GET, PATCH, HEAD, OPTIONS"
    if subpath in {"schema/", "docs/"}:
        return "GET, HEAD, OPTIONS"
    return None


def _is_route(path: str) -> bool:
    try:
        resolve(path)
    except Resolver404:
        return False
    return True


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


def _error(code: str, detail: str, status: int, **headers: str) -> JsonResponse:
    response = JsonResponse(error_body(code, detail), status=status)
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


class ApiContractMiddleware:
    """406 / 415 / 405 {code, detail} on /api/v1/ before and after Ninja."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        subpath = _api_path(request)
        if subpath is None:
            return self.get_response(request)

        allow = _allow_for(subpath)
        method = request.method or "GET"

        # Content negotiation comes first, as in DRF's APIView.initial: an Accept
        # the route cannot produce is 406 before the 405, OPTIONS and the staff
        # and auth checks, even when anonymous. A path that matches no route is
        # Django's 404 whatever the Accept.
        if _is_route(request.path_info) and not _negotiate(
            request.headers.get("Accept"), _produces(subpath)
        ):
            return _not_acceptable()

        if allow is not None and method not in {m.strip() for m in allow.split(",")}:
            return self._not_allowed(method, allow)

        if (
            method in {"POST", "PUT", "PATCH"}
            and allow
            and method in {m.strip() for m in allow.split(",")}
        ):
            content_type = (request.content_type or "").split(";")[0].strip().lower()
            if content_type and content_type not in {
                "application/json",
                "application/octet-stream",
                "",
            }:
                return _error(
                    "unsupported_media_type",
                    f'Unsupported media type "{request.content_type}" in request.',
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                )

        response = self.get_response(request)
        if response.status_code == HTTPStatus.METHOD_NOT_ALLOWED and allow is not None:
            return self._not_allowed(method, allow)
        return response

    def _not_allowed(self, method: str, allow: str) -> HttpResponse:
        body = json.dumps(
            error_body("method_not_allowed", f'Method "{method}" not allowed.')
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


def docs_view(request: HttpRequest) -> HttpResponse:
    """Staff Swagger UI without an inline script (CSP). ?script= returns the init JS."""
    if "script" in request.GET:
        js = (
            "/* Agentic Django Swagger init (split, no inline script) */\n"
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
