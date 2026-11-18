"""The API docs and schema endpoints as a client sees them: access and negotiation."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.urls import reverse

if TYPE_CHECKING:
    from django.test import Client


def _assert_json_error(response, status: HTTPStatus, code: str) -> None:
    assert response.status_code == status
    assert response.headers["Content-Type"].startswith("application/json")
    assert response.json()["code"] == code


def test_api_docs_accessible_by_admin(admin_client) -> None:
    url = reverse("api-docs")
    response = admin_client.get(url)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.django_db
def test_api_docs_and_schema_require_authentication(client) -> None:
    _assert_json_error(
        client.get(reverse("api-docs")),
        HTTPStatus.UNAUTHORIZED,
        "not_authenticated",
    )
    _assert_json_error(
        client.get(reverse("api-schema")),
        HTTPStatus.UNAUTHORIZED,
        "not_authenticated",
    )


@pytest.mark.django_db
def test_api_docs_and_schema_forbid_non_admin_users(user_client: Client) -> None:
    _assert_json_error(
        user_client.get(reverse("api-docs")),
        HTTPStatus.FORBIDDEN,
        "permission_denied",
    )
    _assert_json_error(
        user_client.get(reverse("api-schema")),
        HTTPStatus.FORBIDDEN,
        "permission_denied",
    )


def test_api_docs_json_accept_is_json_406(client) -> None:
    # The docs page is HTML, so Accept: application/json is not acceptable.
    # The error itself must still be the JSON envelope, not an HTML 406.
    _assert_json_error(
        client.get(reverse("api-docs"), HTTP_ACCEPT="application/json"),
        HTTPStatus.NOT_ACCEPTABLE,
        "not_acceptable",
    )


def test_schema_format_comes_from_accept_not_the_query_string(admin_client) -> None:
    # URL_FORMAT_OVERRIDE is off: ?format=json is ignored and the default (YAML)
    # wins. JSON is requested with Accept: application/json.
    response = admin_client.get(reverse("api-schema") + "?format=json")

    assert response.status_code == HTTPStatus.OK
    assert response.headers["Content-Type"].startswith("application/vnd.oai.openapi")
    assert response.content.startswith(b"openapi:")
