from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import override_settings
from django.test.client import BOUNDARY
from django.test.client import MULTIPART_CONTENT
from django.test.client import encode_multipart

from apps.users.services import user_update
from apps.users.tests.factories import UserFactory

if TYPE_CHECKING:
    from django.test import Client

    from apps.users.models import User

pytestmark = pytest.mark.django_db


class TestUserViewSet:
    def test_anonymous_requests_are_rejected(self, user: User, client: Client) -> None:
        list_response = client.get("/api/v1/users/")
        assert list_response.status_code == HTTPStatus.UNAUTHORIZED
        assert list_response.headers["WWW-Authenticate"] == "X-Session-Token"

        assert client.get("/api/v1/users/me/").status_code == HTTPStatus.UNAUTHORIZED
        for path in (f"/api/v1/users/{user.public_id}/", "/api/v1/users/me/"):
            response = client.patch(
                path, {"name": "Denied"}, content_type="application/json"
            )
            assert response.status_code == HTTPStatus.UNAUTHORIZED

    def test_me_returns_authenticated_user(
        self, user: User, user_client: Client
    ) -> None:
        response = user_client.get("/api/v1/users/me/")

        assert response.status_code == HTTPStatus.OK
        assert response.json() == {
            "id": str(user.public_id),
            "url": f"http://testserver/api/v1/users/{user.public_id}/",
            "name": user.name,
        }

    def test_browsers_get_json_not_the_browsable_api(self, user_client: Client) -> None:
        response = user_client.get("/api/v1/users/me/", HTTP_ACCEPT="text/html")

        assert response.status_code == HTTPStatus.NOT_ACCEPTABLE
        assert response.headers["Content-Type"] == "application/json"

    def test_format_query_parameter_is_ignored(self, user_client: Client) -> None:
        response = user_client.get("/api/v1/users/me/?format=xml")

        assert response.status_code == HTTPStatus.OK
        assert response.headers["Content-Type"] == "application/json"
        assert "format=" not in response.json()["url"]

    def test_only_the_accept_header_negotiates_the_format(
        self, user_client: Client
    ) -> None:
        response = user_client.get("/api/v1/users/me/", HTTP_ACCEPT="application/xml")

        assert response.status_code == HTTPStatus.NOT_ACCEPTABLE
        assert response.json()["code"] == "not_acceptable"

    def test_list_is_paginated_and_scoped_to_authenticated_user(
        self,
        user: User,
        user_client: Client,
    ) -> None:
        other = UserFactory()

        response = user_client.get("/api/v1/users/")

        assert response.status_code == HTTPStatus.OK
        assert response.json() == {
            "count": 1,
            "next": None,
            "previous": None,
            "results": [
                {
                    "id": str(user.public_id),
                    "url": f"http://testserver/api/v1/users/{user.public_id}/",
                    "name": user.name,
                },
            ],
        }
        assert f"/api/v1/users/{other.public_id}/" not in str(response.json())

    def test_integer_pk_is_not_a_valid_lookup(
        self, user: User, user_client: Client
    ) -> None:
        response = user_client.get(f"/api/v1/users/{user.pk}/")

        assert response.status_code == HTTPStatus.NOT_FOUND
        assert response.json()["code"] == "not_found"

    @override_settings(DEBUG=True)
    def test_uppercase_public_id_reaches_the_view(
        self, user: User, user_client: Client
    ) -> None:
        response = user_client.get(f"/api/v1/users/{str(user.public_id).upper()}/")

        assert response.status_code == HTTPStatus.OK
        assert response.headers["Content-Type"].startswith("application/json")
        assert response.json()["id"] == str(user.public_id)

    def test_retrieve_other_user_returns_not_found(self, user_client: Client) -> None:
        other = UserFactory()

        response = user_client.get(f"/api/v1/users/{other.public_id}/")

        assert response.status_code == HTTPStatus.NOT_FOUND

    def test_patch_updates_name_through_service(
        self,
        user: User,
        monkeypatch: pytest.MonkeyPatch,
        user_client: Client,
    ) -> None:
        calls: list[tuple[int, str]] = []

        def tracked_user_update(*, user: User, name: str) -> User:
            calls.append((user.pk, name))
            return user_update(user=user, name=name)

        monkeypatch.setattr("apps.users.api.views.user_update", tracked_user_update)

        other = UserFactory()

        response = user_client.patch(
            f"/api/v1/users/{user.public_id}/",
            {"name": "Grace Hopper"},
            content_type="application/json",
        )

        assert response.status_code == HTTPStatus.OK
        assert response.json()["name"] == "Grace Hopper"
        assert calls == [(user.pk, "Grace Hopper")]
        user.refresh_from_db()
        assert user.name == "Grace Hopper"

        forbidden_other = user_client.patch(
            f"/api/v1/users/{other.public_id}/",
            {"name": "Nope"},
            content_type="application/json",
        )
        assert forbidden_other.status_code == HTTPStatus.NOT_FOUND

    def test_patch_me_updates_name_through_service(
        self,
        user: User,
        monkeypatch: pytest.MonkeyPatch,
        user_client: Client,
    ) -> None:
        calls: list[tuple[int, str]] = []

        def tracked_user_update(*, user: User, name: str) -> User:
            calls.append((user.pk, name))
            return user_update(user=user, name=name)

        monkeypatch.setattr("apps.users.api.views.user_update", tracked_user_update)

        response = user_client.patch(
            "/api/v1/users/me/",
            {"name": "Ada Lovelace"},
            content_type="application/json",
        )

        assert response.status_code == HTTPStatus.OK
        assert response.json()["id"] == str(user.public_id)
        assert response.json()["name"] == "Ada Lovelace"
        assert calls == [(user.pk, "Ada Lovelace")]
        user.refresh_from_db()
        assert user.name == "Ada Lovelace"

    def test_patch_rejects_unknown_fields(
        self, user: User, user_client: Client
    ) -> None:
        response = user_client.patch(
            "/api/v1/users/me/",
            {"name": "Typo Fixer", "is_staff": True},
            content_type="application/json",
        )

        assert response.status_code == HTTPStatus.BAD_REQUEST
        assert response.json() == {
            "code": "validation_error",
            "detail": "Invalid input.",
            "fields": {
                "is_staff": [{"code": "unknown_field", "detail": "Unknown field."}],
            },
        }
        user.refresh_from_db()
        assert user.name != "Typo Fixer"
        assert user.is_staff is False

    def test_patch_accepts_a_get_body_sent_back(self, user_client: Client) -> None:
        body = user_client.get("/api/v1/users/me/").json()

        response = user_client.patch(
            "/api/v1/users/me/",
            {**body, "name": "Echoed Back"},
            content_type="application/json",
        )

        assert response.status_code == HTTPStatus.OK
        assert response.json()["name"] == "Echoed Back"

    def test_multipart_body_is_unsupported(
        self, user: User, user_client: Client
    ) -> None:
        response = user_client.patch(
            f"/api/v1/users/{user.public_id}/",
            encode_multipart(BOUNDARY, {"name": "Form"}),
            content_type=MULTIPART_CONTENT,
        )

        assert response.status_code == HTTPStatus.UNSUPPORTED_MEDIA_TYPE
        assert response.json()["code"] == "unsupported_media_type"

    def test_put_is_not_allowed(self, user: User, user_client: Client) -> None:
        response = user_client.put(
            f"/api/v1/users/{user.public_id}/",
            {"name": "Full"},
            content_type="application/json",
        )

        assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED
        assert response.headers["Allow"] == "GET, PATCH, HEAD, OPTIONS"
        assert response.json()["code"] == "method_not_allowed"

    @pytest.mark.parametrize("target", ["detail", "me"])
    def test_patch_converts_service_validation_error_to_bad_request(
        self,
        user: User,
        monkeypatch: pytest.MonkeyPatch,
        target: str,
        user_client: Client,
    ) -> None:
        def failing_user_update(*, user: User, name: str) -> User:
            raise DjangoValidationError({"name": ["Reserved display name."]})

        monkeypatch.setattr("apps.users.api.views.user_update", failing_user_update)

        path = (
            "/api/v1/users/me/"
            if target == "me"
            else f"/api/v1/users/{user.public_id}/"
        )

        response = user_client.patch(
            path, {"name": "Root"}, content_type="application/json"
        )

        assert response.status_code == HTTPStatus.BAD_REQUEST
        assert response.json() == {
            "code": "validation_error",
            "detail": "Invalid input.",
            "fields": {
                "name": [{"code": "invalid", "detail": "Reserved display name."}],
            },
        }
