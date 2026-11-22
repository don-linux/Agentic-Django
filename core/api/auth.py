"""API authentication: allauth X-Session-Token first, then Django session."""

from __future__ import annotations

from allauth.headless.contrib.ninja.security import (
    XSessionTokenAuth as AllauthXSessionTokenAuth,
)
from ninja.security import SessionAuth

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


x_session_token_auth = XSessionTokenAuth()
django_auth = cookieAuth()
API_AUTH: list[XSessionTokenAuth | cookieAuth] = [x_session_token_auth, django_auth]
