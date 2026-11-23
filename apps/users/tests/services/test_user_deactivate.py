"""user_deactivate and user_reactivate, framework-neutral: sessions are made
and read with the session store, never through an HTTP client
(docs/ARCHITECTURE.md#services-and-selectors)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest import mock

import pytest
from django.contrib.sessions.backends.cached_db import KEY_PREFIX as SESSION_PREFIX
from django.contrib.sessions.backends.cached_db import SessionStore
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.core.exceptions import ValidationError
from redis.exceptions import ConnectionError as ValkeyConnectionError

from apps.users.models import User
from apps.users.services import user_deactivate
from apps.users.services import user_reactivate
from core.tests.accounts import verified_user

if TYPE_CHECKING:
    from pytest_django.fixtures import Settings

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


def _session_for(user: User) -> str:
    # What a login leaves behind: a session that names the user.
    store = SessionStore()
    store["_auth_user_id"] = str(user.pk)
    store.create()
    assert store.session_key is not None
    return store.session_key


def _signed_in(key: str) -> bool:
    return Session.objects.filter(session_key=key).exists()


def test_deactivate_sets_the_flag_and_signs_the_user_out() -> None:
    user = verified_user("leaving@example.com")
    key = _session_for(user)

    user_deactivate(user=user)

    user.refresh_from_db()
    assert user.is_active is False
    assert not _signed_in(key)


def test_deactivate_keeps_other_users_sessions() -> None:
    leaving = verified_user("leaving@example.com")
    staying = verified_user("staying@example.com")
    leaving_key = _session_for(leaving)
    staying_key = _session_for(staying)

    user_deactivate(user=leaving)

    assert not _signed_in(leaving_key)
    assert _signed_in(staying_key)


def test_deactivate_deletes_the_cached_session_so_reactivation_cannot_revive_it(
    settings: Settings,
) -> None:
    # With cached_db a session is read from the cache first: deleting only the
    # database row would leave it alive, and reactivating the user would bring
    # every session that was meant to be revoked back to life.
    settings.SESSION_ENGINE = CACHED_DB
    user = verified_user("leaving@example.com")
    key = _session_for(user)
    assert cache.get(f"{SESSION_PREFIX}{key}") is not None

    user_deactivate(user=user)
    user_reactivate(user=user)

    assert cache.get(f"{SESSION_PREFIX}{key}") is None
    assert not _signed_in(key)


def test_deactivate_rolls_back_when_the_cached_session_cannot_be_deleted(
    settings: Settings,
) -> None:
    settings.SESSION_ENGINE = CACHED_DB
    user = verified_user("leaving@example.com")
    key = _session_for(user)
    settings.CACHES = DEAD_VALKEY

    # A session that could not be revoked must not look revoked.
    with pytest.raises(ValkeyConnectionError):
        user_deactivate(user=user)

    user.refresh_from_db()
    assert user.is_active is True
    assert _signed_in(key)


def test_deactivate_rolls_back_when_full_clean_fails() -> None:
    user = verified_user("leaving@example.com")
    key = _session_for(user)

    with (
        mock.patch.object(User, "full_clean", side_effect=ValidationError("bad")),
        pytest.raises(ValidationError),
    ):
        user_deactivate(user=user)

    user.refresh_from_db()
    assert user.is_active is True
    assert _signed_in(key)


def test_reactivate_rolls_back_when_full_clean_fails(user: User) -> None:
    user_deactivate(user=user)

    with (
        mock.patch.object(User, "full_clean", side_effect=ValidationError("bad")),
        pytest.raises(ValidationError),
    ):
        user_reactivate(user=user)

    user.refresh_from_db()
    assert user.is_active is False


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
