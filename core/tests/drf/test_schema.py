from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from django.urls import path
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.utils import OpenApiResponse
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api.serializers import FieldErrorSerializer

if TYPE_CHECKING:
    from rest_framework.request import Request

ERROR_REF = "#/components/schemas/Error"
VALIDATION_ERROR_REF = "#/components/schemas/ValidationError"


class _DeclaredResponsesView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses={
            200: FieldErrorSerializer,
            404: OpenApiResponse(description="Declared by the view."),
        },
    )
    def get(self, request: Request, pk: int) -> Response:
        return Response()  # pragma: no cover


class _PublicUnthrottledView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = []

    @extend_schema(request=FieldErrorSerializer, responses=FieldErrorSerializer)
    def post(self, request: Request) -> Response:
        return Response()  # pragma: no cover


def _operation(view: type[APIView], route: str, method: str) -> dict[str, Any]:
    generator = SchemaGenerator(patterns=[path(route, view.as_view())])
    schema = generator.get_schema(request=None, public=True)
    # drf-spectacular renames a "pk" path parameter to "id".
    return schema["paths"][f"/{route}".replace("<int:pk>", "{id}")][method]


def _json_schema(operation: dict[str, Any], status: str) -> dict[str, Any]:
    return operation["responses"][status]["content"]["application/json"]["schema"]


def test_declared_responses_win_and_defaults_fill_the_rest() -> None:
    operation = _operation(_DeclaredResponsesView, "items/<int:pk>/", "get")

    assert operation["responses"]["404"]["description"] == "Declared by the view."
    assert {"200", "401", "403", "404", "406", "429"} <= set(operation["responses"])
    assert "WWW-Authenticate" in operation["responses"]["401"]["headers"]
    assert "Retry-After" in operation["responses"]["429"]["headers"]


def test_public_unthrottled_write_documents_parse_and_media_errors() -> None:
    operation = _operation(_PublicUnthrottledView, "public/", "post")

    assert set(operation["responses"]) == {"200", "400", "406", "415"}
    assert _json_schema(operation, "400")["oneOf"] == [
        {"$ref": VALIDATION_ERROR_REF},
        {"$ref": ERROR_REF},
    ]
    assert _json_schema(operation, "415") == {"$ref": ERROR_REF}
