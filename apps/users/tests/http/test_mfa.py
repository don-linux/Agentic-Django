"""allauth.mfa stays installed but inert (MFA_SUPPORTED_TYPES is empty)."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import pytest
from django.conf import settings
from django.test import Client

from core.tests.accounts import session_token

pytestmark = pytest.mark.django_db

BASE = "/_allauth/app/v1"
UNMOUNTED = [
    ("get", "/account/authenticators/totp"),
    ("post", "/account/authenticators/totp"),
    ("get", "/account/authenticators/recovery-codes"),
    ("post", "/account/authenticators/recovery-codes"),
]


def _login() -> str:
    return session_token("mfa@example.com")


def _call(method: str, route: str, token: str | None = None) -> Any:
    headers = {"HTTP_X_SESSION_TOKEN": token} if token else {}
    return getattr(Client(), method)(
        f"{BASE}{route}", {"code": "123456"}, content_type="application/json", **headers
    )


def test_no_authenticator_type_is_offered() -> None:
    assert settings.MFA_SUPPORTED_TYPES == []
    assert settings.MFA_TOTP_ISSUER == settings.EMAIL_SIGNATURE_NAME


@pytest.mark.parametrize("authenticated", [False, True])
@pytest.mark.parametrize(("method", "route"), UNMOUNTED)
def test_totp_and_recovery_code_routes_are_not_mounted(
    method: str,
    route: str,
    *,
    authenticated: bool,
) -> None:
    response = _call(method, route, _login() if authenticated else None)

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()["code"] == "not_found"


def test_authenticators_list_is_empty_for_a_user() -> None:
    response = _call("get", "/account/authenticators", _login())

    assert response.status_code == HTTPStatus.OK
    assert response.json()["data"] == []


@pytest.mark.parametrize(
    ("method", "route"),
    [
        ("get", "/account/authenticators"),
        ("post", "/auth/2fa/authenticate"),
        ("post", "/auth/2fa/reauthenticate"),
    ],
)
def test_mounted_routes_ask_an_anonymous_client_to_log_in(
    method: str, route: str
) -> None:
    response = _call(method, route)

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["status"] == HTTPStatus.UNAUTHORIZED


def test_two_factor_authenticate_without_a_pending_flow_is_401() -> None:
    response = _call("post", "/auth/2fa/authenticate", _login())

    assert response.status_code == HTTPStatus.UNAUTHORIZED


def test_two_factor_reauthenticate_without_an_authenticator_is_400() -> None:
    response = _call("post", "/auth/2fa/reauthenticate", _login())

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["errors"][0]["code"] == "incorrect_code"


def test_login_stays_one_step() -> None:
    token = _login()

    session = Client().get(f"{BASE}/auth/session", HTTP_X_SESSION_TOKEN=token)
    assert session.status_code == HTTPStatus.OK
    assert [m["method"] for m in session.json()["data"]["methods"]] == ["password"]
