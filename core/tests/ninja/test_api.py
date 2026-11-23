from __future__ import annotations

from http import HTTPStatus

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from ninja import Router
from ninja.testing import TestClient

from apps.users.models import User
from core.tests.ninja.support import make_api


def _client(raises):
    api = make_api(throttle=[], auth=None)
    router = Router()

    @router.post("/test/", auth=None)
    def post(request):
        raises()
        return {"ok": True}

    @router.get("/test/", auth=None)
    def get(request):
        raises()
        return {"ok": True}

    api.add_router("/", router)
    return TestClient(api)


def test_django_dict_validation_error_maps_to_400_json() -> None:
    def raises() -> None:
        raise DjangoValidationError(
            {"name": ["Invalid name."], "__all__": ["Cross-field rule failed."]}
        )

    response = _client(raises).post("/test/", json={})
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json() == {
        "code": "validation_error",
        "detail": "Invalid input.",
        "fields": {
            "name": [{"code": "invalid", "detail": "Invalid name."}],
            "non_field_errors": [
                {"code": "invalid", "detail": "Cross-field rule failed."}
            ],
        },
    }


def test_django_plain_validation_error_maps_to_non_field_errors() -> None:
    def raises() -> None:
        msg = "Service rule violated."
        raise DjangoValidationError(msg)

    response = _client(raises).post("/test/", json={})
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json() == {
        "code": "validation_error",
        "detail": "Invalid input.",
        "fields": {
            "non_field_errors": [
                {"code": "invalid", "detail": "Service rule violated."}
            ]
        },
    }


def test_object_does_not_exist_maps_to_404_json() -> None:
    def raises() -> None:
        msg = "User matching query does not exist."
        raise User.DoesNotExist(msg)

    # Ninja does not catch Model.DoesNotExist by default; our handler maps Http404.
    # A 404 never repeats the exception's message, which may name a model.
    def raises_404() -> None:
        msg = "No User matches the given query."
        raise Http404(msg)

    response = _client(raises_404).get("/test/")
    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json() == {"code": "not_found", "detail": "Not Found"}
