"""The project NinjaAPI instance, mounted at /api/v1/."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings
from django.urls import register_converter
from ninja import NinjaAPI
from ninja.renderers import JSONRenderer

from core.api.auth import API_AUTH
from core.api.converters import PublicIdConverter
from core.api.exceptions import register_exception_handlers
from core.api.parsers import JSONParser
from core.api.throttling import AnonThrottle
from core.api.throttling import UserThrottle

if TYPE_CHECKING:
    from ninja.errors import ValidationError
    from ninja.errors import ValidationErrorContext


# Registered once per process, here, before a router path uses {public_id:...}.
# Django 6.0 no longer lets register_converter() override a converter, and tests
# reload config.urls, so the URLconf is not the place:
# https://docs.djangoproject.com/en/6.1/releases/6.0/ (removed features) and
# https://docs.djangoproject.com/en/6.1/topics/http/urls/#registering-custom-path-converters
register_converter(PublicIdConverter, "public_id")


class CompactJSONRenderer(JSONRenderer):
    """DRF's JSONRenderer bytes: no spaces after separators, UTF-8 (not \\u escapes)."""

    json_dumps_params = {"separators": (",", ":"), "ensure_ascii": False}


class ContractAPI(NinjaAPI):
    """Match the previous Content-Type: application/json (no charset)."""

    def get_content_type(self) -> str:
        return self.renderer.media_type or "application/json"

    def validation_error_from_error_contexts(
        self, error_contexts: list[ValidationErrorContext[Any]]
    ) -> ValidationError:
        """Keep the Python type of each rejected input (Ninja deletes the input).

        core.api.exceptions uses it for DRF's codes: None is "null", and a body
        that is not an object says what it got. The value itself never leaves.
        """
        error = super().validation_error_from_error_contexts(error_contexts)
        raw = [
            item
            for context in error_contexts
            for item in context.pydantic_validation_error.errors(include_url=False)
        ]
        for item, original in zip(error.errors, raw, strict=True):
            item["input_type"] = type(original.get("input")).__name__
        return error


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
    # Custom schema/docs views keep the existing URL names and YAML default.
    openapi_url=None,
    docs_url=None,
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
