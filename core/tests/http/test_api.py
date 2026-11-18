"""Session-token authentication as a client sees it."""

from __future__ import annotations

from http import HTTPStatus

import pytest
from allauth.account.models import EmailAddress
from django.test import Client

from apps.users.tests.factories import UserFactory


@pytest.mark.django_db
def test_x_session_token_takes_precedence_over_csrf_enforced_session_cookie() -> None:
    password = "My_R@ndom-P@ssw0rd"  # noqa: S105
    token_user = UserFactory(email="token-owner@example.com", password=password)
    EmailAddress.objects.create(
        user=token_user,
        email=token_user.email,
        primary=True,
        verified=True,
    )
    cookie_user = UserFactory(email="cookie-owner@example.com", password=password)

    login_response = Client().post(
        "/_allauth/app/v1/auth/login",
        {"email": token_user.email, "password": password},
        content_type="application/json",
    )
    session_token = login_response.json()["meta"]["session_token"]

    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(cookie_user)

    patched = csrf_client.patch(
        f"/api/v1/users/{token_user.public_id}/",
        {"name": "Token Wins"},
        content_type="application/json",
        HTTP_X_SESSION_TOKEN=session_token,
    )

    assert patched.status_code == HTTPStatus.OK
    token_user.refresh_from_db()
    assert token_user.name == "Token Wins"
