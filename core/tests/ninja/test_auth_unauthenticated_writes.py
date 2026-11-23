"""A write without valid credentials is 401, not a CSRF 403 (DRF parity).

django_auth (ninja SessionAuth) runs check_csrf before it looks at the
session, so an anonymous PATCH or one with a bad X-Session-Token used to
answer 403 "CSRF check Failed". DRF's SessionAuthentication only enforces
CSRF for an authenticated session. django.test.Client skips CSRF unless
enforce_csrf_checks=True, which is why every case here sets it.
"""

from __future__ import annotations

from http import HTTPStatus

import pytest
from allauth.account.models import EmailAddress
from django.test import Client

from apps.users.tests.factories import UserFactory
from core.tests.credentials import PASSWORD

ME_URL = "/api/v1/users/me/"
BAD_TOKEN = "bogus"  # noqa: S105
LOGIN_URL = "/_allauth/app/v1/auth/login"
SESSION_URL = "/_allauth/app/v1/auth/session"


def _verified(email: str):
    user = UserFactory(email=email, password=PASSWORD)
    EmailAddress.objects.create(
        user=user, email=user.email, primary=True, verified=True
    )
    return user


def _token(user) -> str:
    response = Client().post(
        LOGIN_URL,
        {"email": user.email, "password": PASSWORD},
        content_type="application/json",
    )
    assert response.status_code == HTTPStatus.OK, response.content
    return response.json()["meta"]["session_token"]


def _assert_401(response) -> None:
    assert response.status_code == HTTPStatus.UNAUTHORIZED, response.content
    assert response["WWW-Authenticate"] == "X-Session-Token"
    assert response.json() == {
        "code": "not_authenticated",
        "detail": "Authentication credentials were not provided.",
    }


@pytest.mark.django_db
@pytest.mark.parametrize("token", [None, BAD_TOKEN])
def test_write_without_valid_credentials_is_401(token: str | None) -> None:
    headers = {"X-Session-Token": token} if token else {}
    response = Client(enforce_csrf_checks=True).patch(
        ME_URL, {"name": "x"}, content_type="application/json", headers=headers
    )
    _assert_401(response)


@pytest.mark.django_db
def test_write_with_a_logged_out_token_is_401() -> None:
    user = _verified("expired@example.com")
    token = _token(user)
    Client().delete(SESSION_URL, HTTP_X_SESSION_TOKEN=token)
    response = Client(enforce_csrf_checks=True).patch(
        ME_URL,
        {"name": "x"},
        content_type="application/json",
        HTTP_X_SESSION_TOKEN=token,
    )
    _assert_401(response)


@pytest.mark.django_db
def test_authenticated_session_without_csrf_is_still_403() -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(_verified("session@example.com"))
    response = client.patch(ME_URL, {"name": "x"}, content_type="application/json")
    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.json()["code"] == "permission_denied"


@pytest.mark.django_db
def test_bad_token_falls_back_to_a_session_with_csrf() -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(_verified("fallback@example.com"))
    client.get("/admin/login/")
    response = client.patch(
        ME_URL,
        {"name": "Fallback"},
        content_type="application/json",
        HTTP_X_SESSION_TOKEN=BAD_TOKEN,
        HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
    )
    assert response.status_code == HTTPStatus.OK
    assert response.json()["name"] == "Fallback"
