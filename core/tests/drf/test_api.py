from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.permissions import AllowAny
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.users.models import User
from core.api.exceptions import api_exception_handler

if TYPE_CHECKING:
    from rest_framework.response import Response


class _DictValidationErrorView(APIView):
    permission_classes = [AllowAny]

    def post(self, request) -> Response:
        raise DjangoValidationError(
            {
                "name": ["Invalid name."],
                "__all__": ["Cross-field rule failed."],
            },
        )


class _PlainValidationErrorView(APIView):
    permission_classes = [AllowAny]

    def post(self, request) -> Response:
        msg = "Service rule violated."
        raise DjangoValidationError(msg)


class _DoesNotExistView(APIView):
    permission_classes = [AllowAny]

    def get(self, request) -> Response:
        msg = "User matching query does not exist."
        raise User.DoesNotExist(msg)


def test_django_dict_validation_error_maps_to_400_json() -> None:
    request = APIRequestFactory().post("/test/", {}, format="json")
    response = _DictValidationErrorView.as_view()(request)

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.data == {
        "code": "validation_error",
        "detail": "Invalid input.",
        "fields": {
            "name": [{"code": "invalid", "detail": "Invalid name."}],
            "non_field_errors": [
                {"code": "invalid", "detail": "Cross-field rule failed."},
            ],
        },
    }


def test_django_plain_validation_error_maps_to_non_field_errors() -> None:
    request = APIRequestFactory().post("/test/", {}, format="json")
    response = _PlainValidationErrorView.as_view()(request)

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.data == {
        "code": "validation_error",
        "detail": "Invalid input.",
        "fields": {
            "non_field_errors": [
                {"code": "invalid", "detail": "Service rule violated."},
            ],
        },
    }


def test_object_does_not_exist_maps_to_404_json() -> None:
    request = APIRequestFactory().get("/test/")
    response = _DoesNotExistView.as_view()(request)

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.data == {"code": "not_found", "detail": "Not found."}


def test_unhandled_exception_returns_none() -> None:
    assert api_exception_handler(RuntimeError("boom"), {}) is None
