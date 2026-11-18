"""
The staff-only schema and Swagger views, with JSON error bodies.

handler400/403/404/500 live in core.errors: they need no API framework.
See docs/api.md#errors-outside-drf-views.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

from drf_spectacular.views import SpectacularAPIView
from drf_spectacular.views import SpectacularSwaggerSplitView
from rest_framework.renderers import JSONRenderer

if TYPE_CHECKING:
    from rest_framework.request import Request
    from rest_framework.response import Response


class JsonErrorAPIViewMixin:
    """Render 4xx/5xx bodies as the JSON envelope, whatever Accept asked for."""

    def finalize_response(
        self,
        request: Request,
        response: Response,
        *args: object,
        **kwargs: object,
    ) -> Response:
        # APIView.finalize_response overwrites accepted_renderer; set JSON after it.
        response = super().finalize_response(  # type: ignore[misc]
            request, response, *args, **kwargs
        )
        if response.status_code >= HTTPStatus.BAD_REQUEST:
            response.accepted_renderer = JSONRenderer()
            response.accepted_media_type = "application/json"
            response.renderer_context = self.get_renderer_context()  # type: ignore[attr-defined]
        return response


class SchemaView(JsonErrorAPIViewMixin, SpectacularAPIView):
    """Staff-only OpenAPI schema; errors stay application/json."""


class DocsView(JsonErrorAPIViewMixin, SpectacularSwaggerSplitView):
    """Staff-only Swagger UI; errors stay application/json."""
