"""The API docs and schema endpoints as a client sees them: access and negotiation."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest

from core.tests.accounts import session_token

if TYPE_CHECKING:
    from django.test import Client


def _assert_json_error(response, status: HTTPStatus, code: str) -> None:
    assert response.status_code == status
    assert response.headers["Content-Type"].startswith("application/json")
    assert response.json()["code"] == code


def test_api_docs_accessible_by_admin(admin_client) -> None:
    url = "/api/v1/docs/"
    response = admin_client.get(url)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.django_db
def test_api_docs_and_schema_require_authentication(client) -> None:
    _assert_json_error(
        client.get("/api/v1/docs/"),
        HTTPStatus.UNAUTHORIZED,
        "not_authenticated",
    )
    _assert_json_error(
        client.get("/api/v1/schema/"),
        HTTPStatus.UNAUTHORIZED,
        "not_authenticated",
    )


@pytest.mark.django_db
def test_api_docs_and_schema_forbid_non_admin_users(user_client: Client) -> None:
    _assert_json_error(
        user_client.get("/api/v1/docs/"),
        HTTPStatus.FORBIDDEN,
        "permission_denied",
    )
    _assert_json_error(
        user_client.get("/api/v1/schema/"),
        HTTPStatus.FORBIDDEN,
        "permission_denied",
    )


def test_api_docs_json_accept_is_json_406(client) -> None:
    # The docs page is HTML, so Accept: application/json is not acceptable.
    # The error itself must still be the JSON envelope, not an HTML 406.
    _assert_json_error(
        client.get("/api/v1/docs/", HTTP_ACCEPT="application/json"),
        HTTPStatus.NOT_ACCEPTABLE,
        "not_acceptable",
    )


def test_schema_is_json_whatever_the_query_string(admin_client) -> None:
    # The schema is JSON only (ADR 0019); a ?format= parameter changes nothing.
    response = admin_client.get("/api/v1/schema/?format=yaml")

    assert response.status_code == HTTPStatus.OK
    assert response.headers["Content-Type"] == "application/json"
    assert response.json()["openapi"] == "3.1.0"


@pytest.mark.django_db
def test_a_staff_token_opens_the_docs_next_to_another_users_session(
    user_client,
) -> None:
    # The X-Session-Token comes first, as on every API route: a non-staff
    # session cookie in the same browser does not hide a staff token.
    staff_token = session_token("docs-staff@example.com", is_staff=True)
    response = user_client.get(
        "/api/v1/schema/", headers={"X-Session-Token": staff_token}
    )
    assert response.status_code == HTTPStatus.OK
