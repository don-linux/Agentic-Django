from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from allauth.account.models import EmailAddress
from axes.handlers.proxy import AxesProxyHandler
from django.contrib.sessions.backends.cached_db import KEY_PREFIX as SESSION_PREFIX
from django.contrib.sessions.backends.cached_db import SessionStore
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.test import Client
from redis.exceptions import ConnectionError as ValkeyConnectionError

from apps.users.services import user_deactivate
from apps.users.services import user_reactivate
from apps.users.tests.factories import UserFactory
from core.tests.credentials import PASSWORD

if TYPE_CHECKING:
    from pytest_django.fixtures import Settings

    from apps.users.models import User

pytestmark = pytest.mark.django_db

CACHED_DB = "django.contrib.sessions.backends.cached_db"
# Valkey on a closed port: every call to it fails at once, as during an outage.
DEAD_VALKEY = {
    "default": {
        "BACKEND": "core.cache_backend.ValkeyCache",
        "LOCATION": "redis://127.0.0.1:1/15",
        "OPTIONS": {"socket_connect_timeout": 0.05, "socket_timeout": 0.05},
    },
}


def _verified_user(email: str) -> User:
    user = UserFactory(email=email, password=PASSWORD)
    EmailAddress.objects.create(user=user, email=email, primary=True, verified=True)
    return user


def _session_token(user: User) -> str:
    response = Client().post(
        "/_allauth/app/v1/auth/login",
        {"email": user.email, "password": PASSWORD},
        content_type="application/json",
    )
    assert response.status_code == HTTPStatus.OK
    return response.json()["meta"]["session_token"]


def _me(token: str) -> int:
    return Client().get("/api/v1/users/me/", HTTP_X_SESSION_TOKEN=token).status_code


def test_deactivate_sets_the_flag_and_signs_the_user_out() -> None:
    user = _verified_user("leaving@example.com")
    token = _session_token(user)
    assert _me(token) == HTTPStatus.OK

    user_deactivate(user=user)

    user.refresh_from_db()
    assert user.is_active is False
    assert _me(token) == HTTPStatus.UNAUTHORIZED


def test_deactivate_keeps_other_users_sessions() -> None:
    leaving = _verified_user("leaving@example.com")
    staying = _verified_user("staying@example.com")
    _session_token(leaving)
    staying_token = _session_token(staying)

    user_deactivate(user=leaving)

    assert Session.objects.count() == 1
    assert _me(staying_token) == HTTPStatus.OK


def test_deactivate_deletes_the_cached_session_so_reactivation_cannot_revive_it(
    settings: Settings,
) -> None:
    # With cached_db the token is read from the cache first: deleting only the
    # database row would leave it alive, and reactivating the user would bring
    # every token that was meant to be revoked back to life.
    settings.SESSION_ENGINE = CACHED_DB
    user = _verified_user("leaving@example.com")
    token = _session_token(user)
    assert _me(token) == HTTPStatus.OK
    assert cache.get(f"{SESSION_PREFIX}{token}") is not None

    user_deactivate(user=user)
    user_reactivate(user=user)

    assert cache.get(f"{SESSION_PREFIX}{token}") is None
    assert _me(token) == HTTPStatus.UNAUTHORIZED


def test_deactivate_rolls_back_when_the_cached_session_cannot_be_deleted(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.SESSION_ENGINE = CACHED_DB
    settings.CACHES = DEAD_VALKEY
    # django-axes keeps the cache it was built with: build it again on this one.
    monkeypatch.setattr(AxesProxyHandler, "implementation", None)
    user = _verified_user("leaving@example.com")
    token = _session_token(user)

    # A token that could not be revoked must not look revoked.
    with pytest.raises(ValkeyConnectionError):
        user_deactivate(user=user)

    user.refresh_from_db()
    assert user.is_active is True
    assert Session.objects.filter(session_key=token).exists()


def test_reactivate_restores_login(user: User) -> None:
    user_deactivate(user=user)

    user_reactivate(user=user)

    user.refresh_from_db()
    assert user.is_active is True


def test_reactivate_drops_sessions_left_while_inactive(user: User) -> None:
    user_deactivate(user=user)
    # A login that raced with the deactivation and kept its session.
    store = SessionStore()
    store["_auth_user_id"] = str(user.pk)
    store.create()

    user_reactivate(user=user)

    assert not Session.objects.filter(session_key=store.session_key).exists()
