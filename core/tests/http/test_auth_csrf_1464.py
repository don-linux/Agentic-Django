"""Four auth/CSRF cases for vitalik/django-ninja#1464 (not merged in 1.8.0).

1. Token only, no session cookie and no CSRF token.
2. Session cookie with a CSRF token.
3. Token plus a session cookie (the open issue: CSRF must not 403).
4. No credentials at all: 401, not a CSRF 403.

Case 3 also has an older equivalent in test_api.py
(test_x_session_token_takes_precedence_over_csrf_enforced_session_cookie).
Cookie-only writes without CSRF stay covered by
test_session_cookie_patch_without_csrf_is_json_403.
"""

from __future__ import annotations

from http import HTTPStatus

import pytest
from allauth.account.models import EmailAddress
from django.test import Client

from apps.users.tests.factories import UserFactory
from core.tests.credentials import PASSWORD

ME_URL = "/api/v1/users/me/"
LOGIN_URL = "/_allauth/app/v1/auth/login"


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


@pytest.mark.django_db
def test_patch_me_with_token_only_succeeds() -> None:
    user = _verified("token-only@example.com")
    token = _token(user)
    client = Client(enforce_csrf_checks=True)

    response = client.patch(
        ME_URL,
        {"name": "Token Only"},
        content_type="application/json",
        HTTP_X_SESSION_TOKEN=token,
    )

    assert response.status_code == HTTPStatus.OK
    assert response.json()["name"] == "Token Only"


@pytest.mark.django_db
def test_patch_me_with_session_cookie_and_csrf_succeeds() -> None:
    user = _verified("cookie-csrf@example.com")
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    # Ninja views are csrf_exempt at Django middleware, so the cookie is taken
    # from a Django view that still runs CsrfViewMiddleware (the admin login).
    client.get("/admin/login/")
    csrf = client.cookies["csrftoken"].value

    response = client.patch(
        ME_URL,
        {"name": "Cookie CSRF"},
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )

    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["name"] == "Cookie CSRF"


@pytest.mark.django_db
def test_patch_me_with_token_and_session_cookie_does_not_csrf_403() -> None:
    """Issue #1464: a token-authenticated write that also carries a session cookie."""
    user = _verified("token-and-cookie@example.com")
    token = _token(user)
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)

    response = client.patch(
        ME_URL,
        {"name": "Token Plus Cookie"},
        content_type="application/json",
        HTTP_X_SESSION_TOKEN=token,
    )

    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["name"] == "Token Plus Cookie"


@pytest.mark.django_db
def test_patch_me_without_credentials_is_401_not_a_csrf_403() -> None:
    client = Client(enforce_csrf_checks=True)

    response = client.patch(ME_URL, {"name": "Nobody"}, content_type="application/json")

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response["WWW-Authenticate"] == "X-Session-Token"
    assert response.json()["code"] == "not_authenticated"
