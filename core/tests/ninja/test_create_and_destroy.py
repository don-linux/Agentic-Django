"""The users API has no create or destroy; HTTP methods stay in the contract."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from django.test import Client


@pytest.mark.django_db
def test_users_collection_does_not_accept_post(client: Client) -> None:
    response = client.post("/api/v1/users/", data="{}", content_type="application/json")
    assert response.status_code in {
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.METHOD_NOT_ALLOWED,
    }


@pytest.mark.django_db
def test_users_detail_does_not_accept_delete(user, user_client: Client) -> None:
    response = user_client.delete(f"/api/v1/users/{user.public_id}/")
    assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert response["Allow"] == "GET, PATCH, HEAD, OPTIONS"
    assert response.json() == {
        "code": "method_not_allowed",
        "detail": "Method not allowed",
    }
    assert user_client.get(f"/api/v1/users/{user.public_id}/").status_code == (
        HTTPStatus.OK
    )
