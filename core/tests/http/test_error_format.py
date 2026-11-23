from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest

from core.exceptions import ApplicationError

if TYPE_CHECKING:
    from django.test import Client

    from apps.users.models import User


@pytest.mark.django_db
class TestErrorCodesThroughTheApi:
    def test_unauthenticated_is_401_with_header(self, client: Client) -> None:
        response = client.get("/api/v1/users/me/")

        assert response.status_code == HTTPStatus.UNAUTHORIZED
        assert response.headers["WWW-Authenticate"] == "X-Session-Token"
        assert response.json() == {
            "code": "not_authenticated",
            "detail": "Unauthorized",
        }

    def test_invalid_page_is_404_not_found(self, user_client: Client) -> None:
        response = user_client.get("/api/v1/users/?page=99")

        assert response.status_code == HTTPStatus.NOT_FOUND
        assert response.json()["code"] == "not_found"

    def test_unsupported_method_is_405(self, user: User, user_client: Client) -> None:
        response = user_client.delete(f"/api/v1/users/{user.public_id}/")

        assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED
        assert response.json() == {
            "code": "method_not_allowed",
            "detail": "Method not allowed",
        }

    def test_malformed_json_is_parse_error(
        self, user: User, user_client: Client
    ) -> None:
        response = user_client.patch(
            f"/api/v1/users/{user.public_id}/",
            data="{not json",
            content_type="application/json",
        )

        assert response.status_code == HTTPStatus.BAD_REQUEST
        assert response.json()["code"] == "parse_error"

    def test_unsupported_media_type_is_415(
        self, user: User, user_client: Client
    ) -> None:
        response = user_client.patch(
            f"/api/v1/users/{user.public_id}/",
            data="name=Ada",
            content_type="text/plain",
        )

        assert response.status_code == HTTPStatus.UNSUPPORTED_MEDIA_TYPE
        assert response.json()["code"] == "unsupported_media_type"

    def test_not_acceptable_is_406(self, user_client: Client) -> None:
        response = user_client.get("/api/v1/users/me/", HTTP_ACCEPT="application/xml")

        assert response.status_code == HTTPStatus.NOT_ACCEPTABLE
        assert response.json()["code"] == "not_acceptable"

    def test_too_long_name_is_a_field_error(
        self, user: User, user_client: Client
    ) -> None:
        response = user_client.patch(
            f"/api/v1/users/{user.public_id}/",
            {"name": "x" * 256},
            content_type="application/json",
        )

        assert response.status_code == HTTPStatus.BAD_REQUEST
        body = response.json()
        assert body["code"] == "validation_error"
        assert body["fields"]["name"][0] == {
            "code": "string_too_long",
            "detail": "String should have at most 255 characters",
        }


@pytest.mark.parametrize("code", ["Bad Code", "already-exists", "1st", "", "Upper"])
def test_application_error_rejects_codes_that_are_not_snake_case(code: str) -> None:
    with pytest.raises(ValueError, match="snake_case"):
        ApplicationError("Message.", code=code)


def test_application_error_keeps_message_and_defaults_to_400() -> None:
    error = ApplicationError("Message.", code="some_rule")

    assert error.message == "Message."
    assert str(error) == "Message."
    assert error.status_code == HTTPStatus.BAD_REQUEST
    assert error.extra is None
