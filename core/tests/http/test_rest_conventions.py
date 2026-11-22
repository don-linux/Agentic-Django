from __future__ import annotations

import re
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.test import override_settings
from django.urls import get_resolver

from core.tests.routes import _routes

if TYPE_CHECKING:
    from django.test import Client

    from apps.users.models import User

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("method", ["get", "post", "put", "patch", "delete"])
def test_api_path_without_trailing_slash_is_404_not_a_redirect(
    user_client: Client,
    method: str,
) -> None:
    response = getattr(user_client, method)(
        "/api/v1/users/me", {}, content_type="application/json"
    )

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert "Location" not in response.headers
    assert response.json()["code"] == "not_found"


@override_settings(DEBUG=True)
@pytest.mark.parametrize("method", ["post", "patch", "delete"])
def test_api_path_without_trailing_slash_is_404_under_debug(
    user_client: Client,
    method: str,
) -> None:
    # Django's CommonMiddleware raises RuntimeError (500) here under DEBUG.
    response = getattr(user_client, method)(
        "/api/v1/users/me", {}, content_type="application/json"
    )

    assert response.status_code == HTTPStatus.NOT_FOUND


def test_api_root_is_not_routed(user_client: Client) -> None:
    response = user_client.get("/api/v1/")

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()["code"] == "not_found"


def test_admin_paths_still_redirect_to_the_trailing_slash(admin_client: Client) -> None:
    response = admin_client.get("/admin/users/user")

    assert response.status_code == HTTPStatus.MOVED_PERMANENTLY
    assert response.headers["Location"] == "/admin/users/user/"


@pytest.mark.parametrize("authenticated", [True, False])
def test_api_responses_are_not_cacheable_by_default(
    client: Client,
    user: User,
    *,
    authenticated: bool,
) -> None:
    if authenticated:
        client.force_login(user)

    response = client.get("/api/v1/users/me/")

    cache_control = response.headers["Cache-Control"]
    assert "no-store" in cache_control
    assert "private" in cache_control


def test_health_probe_keeps_its_own_cache_header(client: Client) -> None:
    response = client.get("/health/", HTTP_HOST="127.0.0.1")

    assert response.headers["Cache-Control"] == "no-store"


_PATH_PARAMETER = re.compile(r"\(\?P<\w+>[^)]*\)|<(?:\w+:)?\w+>")
_STATIC_SEGMENT = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")


def route_naming_problems(routes: list[str]) -> list[str]:
    """Return the API routes that break the kebab-case / trailing-slash rule."""
    problems = []
    for route in routes:
        if not route.startswith("api/"):
            continue
        normalized = _PATH_PARAMETER.sub("{param}", route)
        segments = [
            part for part in normalized.split("/") if part and part != "{param}"
        ]
        if not normalized.endswith("/") or not all(
            _STATIC_SEGMENT.fullmatch(segment) for segment in segments
        ):
            problems.append(route)
    return problems


def test_api_routes_are_kebab_case_with_trailing_slash() -> None:
    routes = _routes(get_resolver().url_patterns)

    assert any(route.startswith("api/v1/users/") for route in routes)
    assert route_naming_problems(routes) == []


def test_route_naming_check_catches_snake_case_and_missing_slash() -> None:
    routes = [
        "api/v1/users/(?P<public_id>[0-9a-f-]+)/change_name/",
        "api/v1/users/me",
        "api/v1/users/(?P<public_id>[0-9a-f-]+)/change-name/",
        "admin/users_user/",
    ]

    assert route_naming_problems(routes) == [
        "api/v1/users/(?P<public_id>[0-9a-f-]+)/change_name/",
        "api/v1/users/me",
    ]
