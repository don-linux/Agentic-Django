"""
Response components list every property DRF always renders as required.

Without it oasdiff cannot see a removed response field (docs/api.md#breaking-changes).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from django.urls import path
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.utils import extend_schema
from drf_spectacular.validation import validate_schema
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

if TYPE_CHECKING:
    from rest_framework.request import Request


def _components() -> dict[str, Any]:
    schema = SchemaGenerator().get_schema(request=None, public=True)
    return schema["components"]["schemas"]


def test_every_user_field_is_required_in_responses() -> None:
    assert _components()["User"]["required"] == ["id", "name", "url"]


def test_request_components_keep_their_own_required_list() -> None:
    assert "required" not in _components()["PatchedUserRequest"]


def test_a_page_always_has_its_links_even_when_they_are_null() -> None:
    page = _components()["PaginatedUserList"]

    assert page["required"] == ["count", "next", "previous", "results"]
    assert page["properties"]["next"]["nullable"] is True


def test_a_declared_optional_field_stays_optional() -> None:
    error = _components()["Error"]

    assert error["required"] == ["code", "detail"]
    assert "extra" in error["properties"]


class _ToySerializer(serializers.Serializer[Any]):
    always = serializers.CharField()
    with_default = serializers.CharField(required=False, default="x")
    sometimes = serializers.CharField(required=False)
    secret = serializers.CharField(write_only=True)


class _ToyView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = []

    @extend_schema(request=_ToySerializer, responses=_ToySerializer)
    def post(self, request: Request) -> Response:
        return Response()  # pragma: no cover


def test_only_skippable_fields_are_optional_in_a_response() -> None:
    schema = SchemaGenerator(patterns=[path("toy/", _ToyView.as_view())]).get_schema(
        request=None, public=True
    )
    components = schema["components"]["schemas"]

    assert components["_Toy"]["required"] == ["always", "with_default"]
    assert components["_ToyRequest"]["required"] == ["always", "secret"]


def _response_component(
    serializer_class: type[serializers.Serializer[Any]],
) -> dict[str, Any]:
    """The component of a toy endpoint that only responds with the serializer."""

    class _View(APIView):
        permission_classes = [AllowAny]
        throttle_classes = []

        @extend_schema(responses=serializer_class)
        def get(self, request: Request) -> Response:
            return Response()  # pragma: no cover

    schema = SchemaGenerator(patterns=[path("toy/", _View.as_view())]).get_schema(
        request=None, public=True
    )
    validate_schema(schema)
    name = serializer_class.__name__.removesuffix("Serializer")
    return schema["components"]["schemas"][name]


class _OnlySkippableSerializer(serializers.Serializer[Any]):
    note = serializers.CharField(required=False)


class _ForwardSerializer(serializers.Serializer[Any]):
    alpha = serializers.CharField()
    mid = serializers.CharField(required=False, default="x")
    skipped = serializers.CharField(required=False)
    zeta = serializers.CharField(read_only=True)


class _BackwardSerializer(serializers.Serializer[Any]):
    zeta = serializers.CharField(read_only=True)
    skipped = serializers.CharField(required=False)
    mid = serializers.CharField(required=False, default="x")
    alpha = serializers.CharField()


def test_a_response_with_only_skippable_fields_has_no_required_list() -> None:
    # OpenAPI 3.0 rejects an empty required list; _response_component validates.
    assert "required" not in _response_component(_OnlySkippableSerializer)


def test_a_required_list_is_sorted_whatever_the_field_order() -> None:
    forward = _response_component(_ForwardSerializer)
    backward = _response_component(_BackwardSerializer)

    assert forward["required"] == backward["required"] == ["alpha", "mid", "zeta"]


def test_a_cursor_page_always_has_its_links() -> None:
    from core.api.pagination import LimitCursorPagination  # noqa: PLC0415

    page = LimitCursorPagination().get_paginated_response_schema({"type": "array"})

    assert page["required"] == ["next", "previous", "results"]
