"""Writes with CSRF enforced: an X-Session-Token is never turned into a CSRF 403.

The API tries the X-Session-Token before the session cookie
(core.api.auth.API_AUTH), and the cookie's CSRF check runs only for an
authenticated session. So a token write that also carries a session cookie
passes, and a write without valid credentials is a 401, not a CSRF 403
(vitalik/django-ninja#1464, docs/typing.md). django.test.Client skips CSRF
unless enforce_csrf_checks=True, so every case sets it.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import pytest
from django.test import Client

from core.tests.accounts import log_in
from core.tests.accounts import verified_user

pytestmark = pytest.mark.django_db

ME_URL = "/api/v1/users/me/"
SESSION_URL = "/_allauth/app/v1/auth/session"
BAD_TOKEN = "bogus"  # noqa: S105


def _csrf_client() -> Client:
    return Client(enforce_csrf_checks=True)


def _csrf_token(client: Client) -> str:
    # Ninja views are csrf_exempt at Django middleware, so the cookie is taken
    # from a Django view that still runs CsrfViewMiddleware (the admin login).
    client.get("/admin/login/")
    return client.cookies["csrftoken"].value


def _assert_401(response: Any) -> None:
    assert response.status_code == HTTPStatus.UNAUTHORIZED, response.content
    assert response["WWW-Authenticate"] == "X-Session-Token"
    assert response.json() == {"code": "not_authenticated", "detail": "Unauthorized"}


def test_a_token_alone_writes() -> None:
    token = log_in(verified_user("token-only@example.com"))

    response = _csrf_client().patch(
        ME_URL,
        {"name": "Token Only"},
        content_type="application/json",
        HTTP_X_SESSION_TOKEN=token,
    )

    assert response.status_code == HTTPStatus.OK
    assert response.json()["name"] == "Token Only"


def test_a_session_cookie_writes_with_the_csrf_token() -> None:
    client = _csrf_client()
    client.force_login(verified_user("cookie-csrf@example.com"))

    response = client.patch(
        ME_URL,
        {"name": "Cookie CSRF"},
        content_type="application/json",
        HTTP_X_CSRFTOKEN=_csrf_token(client),
    )

    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["name"] == "Cookie CSRF"


def test_a_session_cookie_without_the_csrf_token_is_a_json_403() -> None:
    user = verified_user("cookie-only@example.com")
    client = _csrf_client()
    client.force_login(user)

    response = client.patch(
        ME_URL, {"name": "No CSRF"}, content_type="application/json"
    )

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.headers["Content-Type"].startswith("application/json")
    assert response.json()["code"] == "permission_denied"
    assert "CSRF" in response.json()["detail"]
    user.refresh_from_db()
    assert user.name != "No CSRF"


def test_a_token_wins_over_another_users_session_cookie() -> None:
    # The case of vitalik/django-ninja#1464: the cookie must neither turn the
    # token write into a CSRF 403 nor make it write as the cookie's user.
    token_user = verified_user("token-owner@example.com")
    cookie_user = verified_user("cookie-owner@example.com")
    token = log_in(token_user)
    client = _csrf_client()
    client.force_login(cookie_user)

    response = client.patch(
        ME_URL,
        {"name": "Token Wins"},
        content_type="application/json",
        HTTP_X_SESSION_TOKEN=token,
    )

    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["id"] == str(token_user.public_id)
    token_user.refresh_from_db()
    cookie_user.refresh_from_db()
    assert token_user.name == "Token Wins"
    assert cookie_user.name != "Token Wins"


def test_a_bad_token_falls_back_to_the_session_cookie_and_its_csrf_token() -> None:
    client = _csrf_client()
    client.force_login(verified_user("fallback@example.com"))

    response = client.patch(
        ME_URL,
        {"name": "Fallback"},
        content_type="application/json",
        HTTP_X_SESSION_TOKEN=BAD_TOKEN,
        HTTP_X_CSRFTOKEN=_csrf_token(client),
    )

    assert response.status_code == HTTPStatus.OK
    assert response.json()["name"] == "Fallback"


@pytest.mark.parametrize("token", [None, BAD_TOKEN])
def test_a_write_without_valid_credentials_is_401_not_a_csrf_403(
    token: str | None,
) -> None:
    headers = {"X-Session-Token": token} if token else {}

    response = _csrf_client().patch(
        ME_URL, {"name": "Nobody"}, content_type="application/json", headers=headers
    )

    _assert_401(response)


def test_a_write_with_a_logged_out_token_is_401() -> None:
    token = log_in(verified_user("logged-out@example.com"))
    Client().delete(SESSION_URL, HTTP_X_SESSION_TOKEN=token)

    response = _csrf_client().patch(
        ME_URL,
        {"name": "Nobody"},
        content_type="application/json",
        HTTP_X_SESSION_TOKEN=token,
    )

    _assert_401(response)
