from __future__ import annotations

from typing import TYPE_CHECKING

from allauth.headless.contrib.rest_framework.authentication import (
    XSessionTokenAuthentication,
)
from django.conf import settings
from drf_spectacular.contrib.django_allauth import XSessionTokenAuthenticationScheme
from drf_spectacular.extensions import OpenApiAuthenticationExtension

if TYPE_CHECKING:
    from rest_framework.request import Request


class SessionTokenAuthentication(XSessionTokenAuthentication):
    """
    X-Session-Token authentication that advertises WWW-Authenticate.

    DRF then answers 401, not 403. See docs/api.md#methods-and-status-codes.
    """

    def authenticate_header(self, request: Request) -> str:
        return "X-Session-Token"


class SessionTokenAuthenticationScheme(XSessionTokenAuthenticationScheme):
    target_class = "core.api.authentication.SessionTokenAuthentication"


class SessionCookieScheme(OpenApiAuthenticationExtension):
    """Staff session cookie, with both local and production cookie names documented."""

    target_class = "rest_framework.authentication.SessionAuthentication"
    name = "cookieAuth"
    # Outrank spectacular's SessionScheme (priority -1) for the same target.
    priority = 1

    def get_security_definition(self, auto_schema: object) -> dict[str, str]:
        return {
            "type": "apiKey",
            "in": "cookie",
            "name": settings.SESSION_COOKIE_NAME,
            "description": (
                "Staff session for the admin and Swagger only. The cookie name is "
                "SESSION_COOKIE_NAME (`sessionid` locally, `__Secure-sessionid` in "
                "production). PATCH, POST, PUT and DELETE also require CSRF."
            ),
        }
