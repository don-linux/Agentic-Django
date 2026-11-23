"""A cross-origin browser client can read the contract's response headers.

Cross-origin, a browser shows JavaScript only the CORS-safelisted response
headers and those in Access-Control-Expose-Headers. The contract sends
Location on a 201 and Retry-After on a 429.
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
def test_a_browser_client_may_read_location_and_retry_after(
    user_client: Client, settings: Settings
) -> None:
    settings.CORS_ALLOWED_ORIGINS = [ORIGIN]
    response = user_client.get("/api/v1/users/me/", headers={"Origin": ORIGIN})
    assert response.status_code == HTTPStatus.OK
    assert response["Access-Control-Allow-Origin"] == ORIGIN
    exposed = response["Access-Control-Expose-Headers"].lower().split(", ")
    assert sorted(exposed) == ["location", "retry-after"]


@pytest.mark.django_db
def test_an_unknown_origin_gets_no_cors_headers(user_client: Client) -> None:
    # The negative case: without an allowed origin nothing is exposed.
    response = user_client.get("/api/v1/users/me/", headers={"Origin": ORIGIN})
    assert response.status_code == HTTPStatus.OK
    assert "Access-Control-Expose-Headers" not in response
