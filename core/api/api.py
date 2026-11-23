"""The project NinjaAPI instance, mounted at /api/v1/."""

from __future__ import annotations

from typing import override

from django.conf import settings
from django.urls import register_converter
from ninja import NinjaAPI
from ninja.renderers import JSONRenderer

from core.api.auth import API_AUTH
from core.api.auth import staff_docs_required
from core.api.converters import PublicIdConverter
from core.api.exceptions import register_exception_handlers
from core.api.parsers import JSONParser
from core.api.throttling import AnonThrottle
from core.api.throttling import UserThrottle

# Registered once per process, here, before a router path uses {public_id:...}.
# Django 6.0 no longer lets register_converter() override a converter, and tests
# reload config.urls, so the URLconf is not the place:
# https://docs.djangoproject.com/en/6.1/releases/6.0/ (removed features) and
# https://docs.djangoproject.com/en/6.1/topics/http/urls/#registering-custom-path-converters
register_converter(PublicIdConverter, "public_id")


class CompactJSONRenderer(JSONRenderer):
    """Compact JSON in UTF-8: no spaces after separators, no \\u escapes (RFC 8259,
    section 8.1, makes UTF-8 the encoding). Smaller bodies; see ADR 0021.

    No NaN or Infinity either (RFC 8259, section 6): json.dumps would write
    them as bare tokens, which no JSON parser accepts, so a non-finite float
    is a 500 instead of a body the client cannot read."""

    json_dumps_params = {
        "separators": (",", ":"),
        "ensure_ascii": False,
        "allow_nan": False,
    }


class ContractAPI(NinjaAPI):
    """Content-Type application/json without a charset parameter, which RFC 8259
    (section 11) does not define for JSON. See ADR 0021."""

    @override
    def get_content_type(self) -> str:
        return self.renderer.media_type or "application/json"


api = ContractAPI(
    title=getattr(settings, "NINJA_TITLE", "Project API"),
    description=getattr(
        settings,
        "NINJA_DESCRIPTION",
        "The Project JSON API. Account endpoints under /_allauth/ belong to "
        "django-allauth and keep its own format (docs/api.md).",
    ),
    version=getattr(settings, "NINJA_VERSION", "1.0.0"),
    urls_namespace="api",
    # Ninja's own schema (JSON) and Swagger views, staff-only through the
    # documented docs_decorator (ADR 0019).
    openapi_url="/schema/",
    docs_url="/docs/",
    docs_decorator=staff_docs_required,
    auth=API_AUTH,
    throttle=[AnonThrottle(), UserThrottle()],
    parser=JSONParser(),
    renderer=CompactJSONRenderer(),
    servers=getattr(
        settings, "NINJA_SERVERS", [{"url": "/", "description": "Current host"}]
    ),
    openapi_extra={"openapi": "3.1.0"},
)

register_exception_handlers(api)
