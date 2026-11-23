"""Changing the account's emails asks for the password again (reauthentication)."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import pytest
from allauth.account.models import EmailAddress
from django.core import mail
from django.test import Client

from apps.users.tests.factories import UserFactory
from core.tests.credentials import PASSWORD

pytestmark = pytest.mark.django_db

LOGIN_URL = "/_allauth/app/v1/auth/login"
REAUTH_URL = "/_allauth/app/v1/auth/reauthenticate"
EMAIL_URL = "/_allauth/app/v1/account/email"


def _logged_in(email: str) -> tuple[Client, str]:
    user = UserFactory(email=email, password=PASSWORD)
    EmailAddress.objects.create(user=user, email=email, primary=True, verified=True)
    client = Client()
    response = client.post(
        LOGIN_URL,
        {"email": email, "password": PASSWORD},
        content_type="application/json",
    )
    assert response.status_code == HTTPStatus.OK, response.content
    return client, response.json()["meta"]["session_token"]


def _flow_ids(response: Any) -> set[str]:
    return {flow["id"] for flow in response.json()["data"]["flows"]}


def test_adding_an_email_right_after_login_needs_reauthentication() -> None:
    client, token = _logged_in("reauth-add@example.com")

    response = client.post(
        EMAIL_URL,
        {"email": "attacker@example.com"},
        content_type="application/json",
        HTTP_X_SESSION_TOKEN=token,
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert "reauthenticate" in _flow_ids(response)
    assert not EmailAddress.objects.filter(email="attacker@example.com").exists()


def test_promoting_an_email_needs_reauthentication() -> None:
    client, token = _logged_in("reauth-primary@example.com")
    user = EmailAddress.objects.get(email="reauth-primary@example.com").user
    EmailAddress.objects.create(
        user=user, email="second@example.com", primary=False, verified=True
    )

    response = client.patch(
        EMAIL_URL,
        {"email": "second@example.com", "primary": True},
        content_type="application/json",
        HTTP_X_SESSION_TOKEN=token,
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    user.refresh_from_db()
    assert user.email == "reauth-primary@example.com"


def test_the_password_unlocks_email_changes() -> None:
    client, token = _logged_in("reauth-ok@example.com")

    reauth = client.post(
        REAUTH_URL,
        {"password": PASSWORD},
        content_type="application/json",
        HTTP_X_SESSION_TOKEN=token,
    )
    added = client.post(
        EMAIL_URL,
        {"email": "added@example.com"},
        content_type="application/json",
        HTTP_X_SESSION_TOKEN=token,
    )

    assert reauth.status_code == HTTPStatus.OK
    assert added.status_code == HTTPStatus.OK
    # Verification is by code: the address is stored once the code is entered.
    assert mail.outbox[-1].to == ["added@example.com"]
