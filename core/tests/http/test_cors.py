"""A cross-origin browser client can read the contract's response headers.

Cross-origin, a browser shows JavaScript only the CORS-safelisted response
headers and those in Access-Control-Expose-Headers. The contract sends
Location on a 201, Retry-After on a 429, an ETag for If-Match, and
Deprecation, Sunset and Link on a deprecated operation.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from django.test import Client
    from pytest_django.fixtures import Settings

ORIGIN = "https://frontend.example.com"


@pytest.mark.django_db
def test_a_browser_client_may_read_the_contract_headers(
    user_client: Client, settings: Settings
) -> None:
    settings.CORS_ALLOWED_ORIGINS = [ORIGIN]
    response = user_client.get("/api/v1/users/me/", headers={"Origin": ORIGIN})
    assert response.status_code == HTTPStatus.OK
    assert response["Access-Control-Allow-Origin"] == ORIGIN
    exposed = response["Access-Control-Expose-Headers"].lower().split(", ")
    assert sorted(exposed) == [
        "deprecation",
        "etag",
        "link",
        "location",
        "retry-after",
        "sunset",
    ]


@pytest.mark.django_db
def test_a_browser_client_may_send_if_match(client: Client, settings: Settings) -> None:
    settings.CORS_ALLOWED_ORIGINS = [ORIGIN]
    response = client.options(
        "/api/v1/users/me/",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "if-match, x-session-token",
        },
    )
    allowed = response["Access-Control-Allow-Headers"].lower().split(", ")
    assert {"if-match", "x-session-token"} <= set(allowed)


@pytest.mark.django_db
def test_an_unknown_origin_gets_no_cors_headers(user_client: Client) -> None:
    # The negative case: without an allowed origin nothing is exposed.
    response = user_client.get("/api/v1/users/me/", headers={"Origin": ORIGIN})
    assert response.status_code == HTTPStatus.OK
    assert "Access-Control-Expose-Headers" not in response
