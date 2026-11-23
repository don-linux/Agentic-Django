"""Accounts for tests: a user with a verified email, logged in like an app client."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Any

from allauth.account.models import EmailAddress
from django.test import Client

from apps.users.tests.factories import UserFactory
from core.tests.credentials import PASSWORD

if TYPE_CHECKING:
    from apps.users.models import User

LOGIN_URL = "/_allauth/app/v1/auth/login"


def verified_user(email: str, *, password: str = PASSWORD, **fields: Any) -> User:
    """Create a user whose primary email is verified, as after signup."""
    user = UserFactory(email=email, password=password, **fields)
    EmailAddress.objects.create(
        user=user, email=user.email, primary=True, verified=True
    )
    return user


def log_in(user: User, password: str = PASSWORD) -> str:
    """Log `user` in through allauth headless and return its X-Session-Token."""
    response = Client().post(
        LOGIN_URL,
        {"email": user.email, "password": password},
        content_type="application/json",
    )
    assert response.status_code == HTTPStatus.OK, response.content
    return response.json()["meta"]["session_token"]


def session_token(email: str, **fields: Any) -> str:
    """Create a user with a verified email, log it in and return its X-Session-Token."""
    return log_in(verified_user(email, **fields))
