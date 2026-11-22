"""django-axes locks the admin login only; allauth headless logins are untouched."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from allauth.account.models import EmailAddress
from django.conf import settings
from django.test import Client
from django.urls import reverse

from apps.users.tests.factories import UserFactory
from core.tests.credentials import PASSWORD

if TYPE_CHECKING:
    from collections.abc import Callable

    from apps.users.models import User

pytestmark = pytest.mark.django_db

WRONG_PASSWORD = "Wrong-H0rse-Battery"  # noqa: S105
HEADLESS_LOGIN = "/_allauth/app/v1/auth/login"


@pytest.fixture
def staff() -> User:
    user = UserFactory(
        email="staff@example.com",
        password=PASSWORD,
        is_staff=True,
        is_superuser=True,
    )
    EmailAddress.objects.create(
        user=user, email=user.email, primary=True, verified=True
    )
    return user


def _admin_login(client: Client, password: str) -> int:
    response = client.post(
        reverse("admin:login"),
        {"username": "staff@example.com", "password": password},
    )
    return response.status_code


def _logged_in(client: Client) -> bool:
    return "_auth_user_id" in client.session


def _fail_admin_logins(client: Client) -> None:
    for _ in range(settings.AXES_FAILURE_LIMIT):
        _admin_login(client, WRONG_PASSWORD)


def test_admin_login_locks_after_failure_limit(staff: User) -> None:
    client = Client()
    _fail_admin_logins(client)

    status = _admin_login(client, PASSWORD)

    assert status == HTTPStatus.TOO_MANY_REQUESTS
    assert not _logged_in(client)


def test_admin_lockout_ends_when_counters_expire(
    staff: User,
    clear_caches: Callable[[], None],
) -> None:
    client = Client()
    _fail_admin_logins(client)
    # The cache handler keeps counters in the cache; clearing it stands in for
    # AXES_COOLOFF_TIME passing.
    clear_caches()

    status = _admin_login(client, PASSWORD)

    assert status == HTTPStatus.FOUND
    assert _logged_in(client)


def test_admin_lockout_does_not_block_headless_login(
    staff: User, client: Client
) -> None:
    _fail_admin_logins(Client())

    response = client.post(
        HEADLESS_LOGIN,
        {"email": staff.email, "password": PASSWORD},
        content_type="application/json",
    )

    assert response.status_code == HTTPStatus.OK


def test_failed_headless_logins_do_not_count_toward_admin_lockout(
    staff: User, client: Client
) -> None:
    for _ in range(settings.AXES_FAILURE_LIMIT):
        response = client.post(
            HEADLESS_LOGIN,
            {"email": staff.email, "password": WRONG_PASSWORD},
            content_type="application/json",
        )
        # allauth's own JSON error, never an axes lockout (plain-text 429).
        assert response.status_code == HTTPStatus.BAD_REQUEST
    client = Client()

    status = _admin_login(client, PASSWORD)

    assert status == HTTPStatus.FOUND
    assert _logged_in(client)
