"""API rules a client can see: no integer pk routes, unknown query params ignored."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.urls import get_resolver

from core.tests.routes import _routes
from core.tests.routes import api_integer_pk_route_problems

if TYPE_CHECKING:
    from django.test import Client
pytestmark = pytest.mark.django_db


def test_unknown_list_query_params_are_ignored(
    user_client: Client,
) -> None:
    plain = user_client.get("/api/v1/users/")
    noisy = user_client.get(
        "/api/v1/users/?ordering=password&search=x&name=Dev",
    )

    assert plain.status_code == HTTPStatus.OK
    assert noisy.status_code == HTTPStatus.OK
    assert noisy.json() == plain.json()


def test_api_routes_do_not_capture_integer_pk() -> None:
    assert api_integer_pk_route_problems(_routes(get_resolver().url_patterns)) == []
