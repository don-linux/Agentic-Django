from __future__ import annotations

import os
from itertools import pairwise
from typing import TYPE_CHECKING

import pytest
from django.conf import settings as django_settings
from django.core.cache import caches
from django.core.cache.backends.locmem import LocMemCache
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from apps.users.tests.factories import UserFactory

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

    from django.test import override_settings
    from pytest_django.fixtures import DjangoCaptureOnCommitCallbacks
    from pytest_django.fixtures import Settings

    from apps.users.models import User

    ConstantQueriesCheck = Callable[[Callable[[], object], Callable[[], object]], None]
    CacheWrites = Callable[[], AbstractContextManager[list[Callable[[], object]]]]
    ThrottleRates = Callable[[dict[str, str]], None]
    TrustedProxies = Callable[[int], override_settings]

# Fixture modules: the adapters between framework-neutral tests and Ninja
# (throttle_rates, trusted_proxies) and the real-Valkey fixtures.
pytest_plugins = ["core.tests.ninja.adapters", "core.tests.valkey_fixtures"]

# Celery reads this variable before the Django setting, so a shell that exports it
# would point the tests at a real broker in spite of config/settings/test.py.
os.environ.pop("CELERY_BROKER_URL", None)

# The test layers of docs/development.md#test-layout, one marker each.
LAYERS = ("http", "ninja")


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Mark each test under a tests/http/ or tests/ninja/ directory with that layer."""
    for item in items:
        parts = item.path.relative_to(config.rootpath).parts
        for parent, child in pairwise(parts):
            if parent == "tests" and child in LAYERS:
                item.add_marker(child)
                break


@pytest.fixture(autouse=True)
def _media_storage(settings: Settings) -> None:
    # Uploads go to memory: tests never touch the disk.
    settings.STORAGES = {
        **settings.STORAGES,
        "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
        "private": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    }


@pytest.fixture
def clear_caches() -> Callable[[], None]:
    """
    Return a function that empties every configured cache.

    It refuses any backend that is not in memory: on Valkey, clear() is FLUSHDB,
    which would wipe the sessions, rate limits and throttles of a real server.
    """

    def clear() -> None:
        for alias in django_settings.CACHES:
            backend = caches[alias]
            if not isinstance(backend, LocMemCache):
                message = (
                    f"cache {alias!r} is not in memory: tests never clear a server"
                )
                raise TypeError(message)
            backend.clear()

    return clear


@pytest.fixture(autouse=True)
def _clear_cache(clear_caches: Callable[[], None]) -> None:
    clear_caches()


@pytest.fixture
def cache_writes(
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> CacheWrites:
    """
    Return a context manager that commits, for the cache, what runs inside it.

    See docs/development.md#cache-writes.
    """

    def committed() -> AbstractContextManager[list[Callable[[], object]]]:
        return django_capture_on_commit_callbacks(execute=True)

    return committed


@pytest.fixture
def user(db: None) -> User:
    return UserFactory.create()


@pytest.fixture
def user_client(user: User) -> Client:
    """
    A Django test client logged in as `user`, independent of `client`.

    force_login() opens a real session, so requests go through the same
    authentication as a browser session. Unauthenticated tests use pytest-django's
    `client`.
    """
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def assert_constant_queries() -> ConstantQueriesCheck:
    """
    Return a check that fails when a request's query count grows with the data.

    `check(call, grow)` runs `call()`, then `grow()` to add rows, then `call()`
    again, and asserts both runs made the same number of queries (an N+1 guard).
    """

    def check(call: Callable[[], object], grow: Callable[[], object]) -> None:
        with CaptureQueriesContext(connection) as small:
            call()
        grow()
        with CaptureQueriesContext(connection) as large:
            call()
        assert len(small) == len(large), (
            f"query count grew from {len(small)} to {len(large)} when data grew: N+1?"
        )

    return check
