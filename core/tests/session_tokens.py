"""Log a user in through allauth headless, the way an app client does."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from allauth.account.models import EmailAddress
from django.test import Client

from apps.users.tests.factories import UserFactory
from core.tests.credentials import PASSWORD

LOGIN_URL = "/_allauth/app/v1/auth/login"


def session_token(email: str, **fields: Any) -> str:
    """Create a user with a verified email, log it in and return its X-Session-Token."""
    user = UserFactory(email=email, password=PASSWORD, **fields)
    EmailAddress.objects.create(
        user=user, email=user.email, primary=True, verified=True
    )
    response = Client().post(
        LOGIN_URL,
        {"email": user.email, "password": PASSWORD},
        content_type="application/json",
    )
    assert response.status_code == HTTPStatus.OK, response.content
    return response.json()["meta"]["session_token"]
