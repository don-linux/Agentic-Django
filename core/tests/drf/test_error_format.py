"""The DRF exception handler maps each exception to the uniform error body."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.db.models import RestrictedError
from rest_framework import serializers
from rest_framework.exceptions import Throttled
from rest_framework.permissions import AllowAny
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from core.api.exceptions import api_exception_handler
from core.exceptions import ApplicationError
from core.exceptions import ConflictError

if TYPE_CHECKING:
    from rest_framework.request import Request
    from rest_framework.response import Response


def _call(view: type[APIView], method: str = "post", data: object = None) -> Response:
    factory = APIRequestFactory()
    request = getattr(factory, method)("/test/", data or {}, format="json")
    return view.as_view()(request)


class _AlreadyExistsView(APIView):
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        msg = "Item already exists."
        raise ApplicationError(msg, code="already_exists", status_code=409)


class _ConflictWithExtraView(APIView):
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        msg = "Stock is not enough."
        raise ConflictError(msg, code="insufficient_stock", extra={"available": 2})


class _ProtectedView(APIView):
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        msg = "Cannot delete."
        raise ProtectedError(msg, set())


class _RestrictedView(APIView):
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        msg = "Cannot delete."
        raise RestrictedError(msg, set())


class _DrfPermissionDeniedView(APIView):
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        raise DjangoPermissionDenied


class _ThrottledView(APIView):
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        raise Throttled(wait=7)


class _AddressSerializer(serializers.Serializer[dict[str, str]]):
    city = serializers.CharField(max_length=5)


class _OrderSerializer(serializers.Serializer[dict[str, object]]):
    address = _AddressSerializer()
    tags = serializers.ListField(child=serializers.IntegerField())

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        msg = "Orders are closed."
        raise serializers.ValidationError(msg, code="orders_closed")


class _OrderView(APIView):
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        serializer = _OrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raise AssertionError  # pragma: no cover


class _ListValidationErrorView(APIView):
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        msg = "Top-level rule failed."
        raise serializers.ValidationError([msg], code="top_level")


def test_application_error_maps_to_its_status_and_code() -> None:
    response = _call(_AlreadyExistsView)

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.data == {"code": "already_exists", "detail": "Item already exists."}


def test_conflict_error_defaults_to_409_and_includes_extra() -> None:
    response = _call(_ConflictWithExtraView)

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.data == {
        "code": "insufficient_stock",
        "detail": "Stock is not enough.",
        "extra": {"available": 2},
    }


@pytest.mark.parametrize("view", [_ProtectedView, _RestrictedView])
def test_protected_and_restricted_errors_map_to_409(view: type[APIView]) -> None:
    response = _call(view)

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.data == {
        "code": "protected",
        "detail": "This resource has dependent records and cannot be deleted.",
    }


def test_integrity_error_is_not_mapped() -> None:
    assert api_exception_handler(IntegrityError("duplicate key"), {}) is None


def test_django_permission_denied_maps_to_permission_denied() -> None:
    response = _call(_DrfPermissionDeniedView)

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.data["code"] == "permission_denied"


def test_throttled_keeps_retry_after_header() -> None:
    response = _call(_ThrottledView)

    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert response.data["code"] == "throttled"
    assert response.headers["Retry-After"] == "7"


def test_nested_and_list_field_errors_keep_their_shape() -> None:
    response = _call(
        _OrderView, data={"address": {"city": "Guadalajara"}, "tags": ["x"]}
    )

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.data == {
        "code": "validation_error",
        "detail": "Invalid input.",
        "fields": {
            "address": {
                "city": [
                    {
                        "code": "max_length",
                        "detail": "Ensure this field has no more than 5 characters.",
                    },
                ],
            },
            "tags": {
                "0": [{"code": "invalid", "detail": "A valid integer is required."}]
            },
        },
    }


def test_object_level_validation_error_goes_to_non_field_errors() -> None:
    response = _call(_OrderView, data={"address": {"city": "León"}, "tags": [1]})

    assert response.data["fields"] == {
        "non_field_errors": [{"code": "orders_closed", "detail": "Orders are closed."}],
    }


def test_top_level_list_validation_error_goes_to_non_field_errors() -> None:
    response = _call(_ListValidationErrorView)

    assert response.data["fields"] == {
        "non_field_errors": [{"code": "top_level", "detail": "Top-level rule failed."}],
    }
