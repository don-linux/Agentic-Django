"""API authentication: allauth X-Session-Token first, then Django session."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Any
from typing import override

from allauth.headless.contrib.ninja.security import (
    XSessionTokenAuth as AllauthXSessionTokenAuth,
)
from ninja.errors import AuthenticationError
from ninja.errors import AuthorizationError
from ninja.security import SessionAuth

from core.errors import json_error

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.http import HttpRequest
    from django.http import HttpResponse

# Token first so a request that also carries a session cookie is not failed by
# django_auth's CSRF check (vitalik/django-ninja#1464; PR #1852 is not in 1.8.0).
# Sources: allauth 65.22.1 allauth.headless.contrib.ninja.security;
# Context7 /pennersr/django-allauth "x_session_token_auth";
# django-ninja 1.8.0 ninja/security/session.py, ninja/security/base.py
# (AuthBase copies openapi_* into the scheme) and ninja/operation.py
# (_run_authentication stops at the first truthy result).
# Component names are the class names (ninja/openapi/schema.py).


class XSessionTokenAuth(AllauthXSessionTokenAuth):
    openapi_type = "apiKey"
    openapi_in = "header"
    openapi_name = "X-Session-Token"
    openapi_description = "X-Session-Token authentication"


class cookieAuth(SessionAuth):  # noqa: N801
    """Staff session for the admin and Swagger. Name kept for the OpenAPI contract."""

    openapi_type = "apiKey"
    openapi_in = "cookie"
    # No openapi_name: APIKeyBase.__init__ sets it from param_name, which is
    # settings.SESSION_COOKIE_NAME (ninja/security/apikey.py, session.py).
    openapi_description = (
        "Staff session for the admin and Swagger only. The cookie name is "
        "SESSION_COOKIE_NAME (`sessionid` locally, `__Secure-sessionid` in "
        "production). PATCH, POST, PUT and DELETE also require CSRF."
    )

    @override
    def __call__(self, request: HttpRequest) -> Any:
        # Auth objects are public callables (guide/authentication/). Check the
        # session before delegating to SessionAuth's public callable: all cookie
        # extraction, CSRF and per-operation csrf_exempt stay native Ninja.
        if not request.user.is_authenticated:
            return None
        return super().__call__(request)


x_session_token_auth = XSessionTokenAuth()
django_auth = cookieAuth()
API_AUTH: list[XSessionTokenAuth | cookieAuth] = [x_session_token_auth, django_auth]


def staff_docs_required(
    view: Callable[..., HttpResponse],
) -> Callable[..., HttpResponse]:
    """
    The NinjaAPI docs_decorator: staff-only schema and docs, with the API's JSON
    401 and 403 (https://django-ninja.dev/guide/openapi/#protecting-the-docs).

    staff_member_required would redirect to the admin login. Session or
    X-Session-Token is accepted; the token goes through the API's own
    x_session_token_auth, which sets request.user, as on every other route.
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
            # The texts of Ninja's own AuthenticationError and AuthorizationError.
            response = json_error(
                "not_authenticated",
                AuthenticationError().message,
                HTTPStatus.UNAUTHORIZED,
            )
            response["WWW-Authenticate"] = "X-Session-Token"
            return response
        if not getattr(user, "is_staff", False):
            return json_error(
                "permission_denied",
                AuthorizationError().message,
                HTTPStatus.FORBIDDEN,
            )
        return view(request, *args, **kwargs)

    return wrapped
