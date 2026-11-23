"""
allauth's headless contract as the template ships it (docs/api.md, Out of scope).

These pin allauth's behaviour, not the template's: if an upgrade changes one,
docs/api.md and the clients must change with it.
"""

from __future__ import annotations

import time
from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Any

import pytest
from allauth.account.models import EmailAddress
from django.test import Client
from django.test import override_settings
from django.urls import get_resolver

from apps.users.tests.factories import UserFactory
from core.tests.credentials import PASSWORD
from core.tests.routes import _routes

if TYPE_CHECKING:
    from apps.users.models import User

pytestmark = pytest.mark.django_db

LOGIN_URL = "/_allauth/app/v1/auth/login"
RESET_REQUEST_URL = "/_allauth/app/v1/auth/password/request"
RESEND_URL = "/_allauth/app/v1/auth/email/verify/resend"
SESSION_URL = "/_allauth/app/v1/auth/session"


def allauth_slashed_leaf_routes(routes: list[str]) -> list[str]:
    """Return /_allauth/ leaf routes that end with a slash (they should not)."""
    return [
        route
        for route in routes
        if route.startswith("_allauth/") and route.endswith("/")
    ]


def test_allauth_headless_leaf_routes_have_no_trailing_slash() -> None:
    slashed = allauth_slashed_leaf_routes(_routes(get_resolver().url_patterns))
    assert slashed == []


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", LOGIN_URL),
        ("get", SESSION_URL),
        ("post", RESET_REQUEST_URL),
    ],
)
def test_allauth_path_with_a_trailing_slash_is_json_404_not_a_redirect(
    client: Client,
    method: str,
    path: str,
) -> None:
    response = getattr(client, method)(f"{path}/", {}, content_type="application/json")

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert "Location" not in response.headers
    assert response.headers["Content-Type"].startswith("application/json")
    assert response.json()["code"] == "not_found"


@override_settings(DEBUG=True)
def test_allauth_login_with_slash_is_404_under_debug(client: Client) -> None:
    response = client.post(f"{LOGIN_URL}/", {}, content_type="application/json")

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert "Location" not in response.headers


def test_allauth_login_without_slash_is_not_404(client: Client) -> None:
    response = client.post(
        LOGIN_URL,
        {"email": "nobody@example.com", "password": "x"},
        content_type="application/json",
    )

    assert response.status_code != HTTPStatus.NOT_FOUND
    assert response.status_code == HTTPStatus.BAD_REQUEST


def _verified_user(*, email: str, password: str) -> User:
    user = UserFactory(email=email, password=password)
    EmailAddress.objects.create(
        user=user,
        email=user.email,
        primary=True,
        verified=True,
    )
    return user


def test_password_reset_429_is_bare_status_without_retry_after(
    settings: Any,
    client: Client,
) -> None:
    settings.ACCOUNT_RATE_LIMITS = {"reset_password": "100/m/ip,2/m/key,100/h/key"}
    user = _verified_user(email="reset@example.com", password=PASSWORD)

    responses = [
        client.post(
            RESET_REQUEST_URL, {"email": user.email}, content_type="application/json"
        )
        for _ in range(3)
    ]

    assert [r.status_code for r in responses] == [
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.TOO_MANY_REQUESTS,
    ]
    limited = responses[2]
    assert limited.json() == {"status": 429}
    assert "Retry-After" not in limited.headers


def test_login_lockout_is_400_too_many_login_attempts_without_retry_after(
    settings: Any,
    client: Client,
) -> None:
    settings.ACCOUNT_RATE_LIMITS = {"login_failed": "2/m/key"}
    user = _verified_user(email="lock@example.com", password=PASSWORD)

    responses = [
        client.post(
            LOGIN_URL,
            {"email": user.email, "password": "Wrong-P@ssw0rd!"},
            content_type="application/json",
        )
        for _ in range(3)
    ]

    assert responses[0].status_code == HTTPStatus.BAD_REQUEST
    assert responses[0].json()["errors"][0]["code"] == "email_password_mismatch"
    limited = responses[2]
    assert limited.status_code == HTTPStatus.BAD_REQUEST
    assert limited.json()["status"] == HTTPStatus.BAD_REQUEST
    assert limited.json()["errors"][0]["code"] == "too_many_login_attempts"
    assert "Retry-After" not in limited.headers


def test_verify_resend_without_pending_flow_is_bare_409(client: Client) -> None:
    response = client.post(RESEND_URL, {}, content_type="application/json")

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json() == {"status": 409}


@override_settings(TIME_ZONE="America/Mexico_City")
def test_allauth_login_method_at_is_a_unix_timestamp(client: Client) -> None:
    password = PASSWORD
    user = _verified_user(email="ts@example.com", password=password)
    before = time.time()

    response = client.post(
        LOGIN_URL,
        {"email": user.email, "password": password},
        content_type="application/json",
    )

    after = time.time()
    assert response.status_code == HTTPStatus.OK
    at = response.json()["data"]["methods"][0]["at"]
    assert isinstance(at, float)
    assert before - 1 <= at <= after + 1
    assert "Z" not in str(at)
    assert response.json()["data"]["user"]["id"] == str(user.public_id)


def test_no_allauth_route_answers_500_to_an_anonymous_request() -> None:
    client = Client(raise_request_exception=False)
    routes = [
        route
        for route in _routes(get_resolver().url_patterns)
        if route.startswith("_allauth/") and "<" not in route
    ]
    methods = ("get", "post", "put", "patch", "delete", "options", "head", "trace")

    errors = [
        f"{method.upper()} /{route}"
        for route in routes
        for method in methods
        if getattr(client, method)(f"/{route}").status_code
        >= HTTPStatus.INTERNAL_SERVER_ERROR
    ]

    assert routes
    assert errors == []
