"""
The real-Valkey fixtures of the `valkey` tests; the root conftest.py loads them.

A test that asks for `valkey` gets the default cache on the dev-stack Valkey and
fails, never skips, without it.
"""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING
from typing import Any

import pytest
from axes.handlers.proxy import AxesProxyHandler
from django.conf import settings as django_settings
from django.core.cache import caches
from redis import ConnectionPool
from redis import Redis
from redis.exceptions import RedisError

from config.settings.base import build_valkey_cache
from core.cache_backend import ValkeyCache

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Iterator

    from pytest_django.fixtures import Settings

# Never 0 (the cache) or 1 (the broker) of a server that may be in use.
CACHE_DB = 15
HOW_TO_START = (
    "Start it with: docker compose -f docker/dev/compose.yaml up -d valkey. Inside "
    "the django container VALKEY_URL is already set; on the host export "
    "VALKEY_URL=redis://localhost:6379. To run the tests that need no server: "
    'pytest -m "not valkey".'
)


@pytest.fixture(scope="module")
def server_url() -> str:
    url = os.environ.get("VALKEY_URL")
    if not url:
        pytest.fail(f"VALKEY_URL is not set: these tests need a Valkey. {HOW_TO_START}")
    return url


@pytest.fixture(scope="module")
def run_prefix() -> str:
    """A key prefix no other run, project or environment uses."""
    return f"it-{uuid.uuid4().hex[:12]}"


@pytest.fixture
def cache_config(server_url: str, run_prefix: str) -> Iterator[dict[str, Any]]:
    """The CACHES entry of one test; the pools its backends open close after it."""
    pools: list[ConnectionPool] = []

    class ClosedAfterTheTest(ConnectionPool):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            pools.append(self)

    config = _server_config(server_url)
    # Django's Redis backend opens its pools with OPTIONS["pool_class"]
    # (docs/topics/cache, "Cache arguments"): one pool per server it reaches.
    options = {**config["OPTIONS"], "pool_class": ClosedAfterTheTest}
    yield {**config, "KEY_PREFIX": run_prefix, "OPTIONS": options}
    # Close their sockets instead of leaving them to the garbage collector.
    for pool in pools:
        pool.disconnect()


@pytest.fixture
def valkey_client(server_url: str) -> Iterator[Redis]:
    """
    A redis-py client of the tests' own, on the database of the cache.

    Django's cache API has no TTL, SCAN or ACL command: the tests read and clean
    the server through this client, never through the backend's own.
    """
    config = _server_config(server_url)
    client = Redis.from_url(config["LOCATION"], **config["OPTIONS"])
    yield client
    client.close()


@pytest.fixture
def valkey(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    cache_config: dict[str, Any],
    valkey_client: Redis,
    run_prefix: str,
) -> Iterator[ValkeyCache]:
    """Make the default cache a real Valkey for one test, then remove its keys."""
    settings.CACHES = {"default": cache_config}
    # django-axes keeps the cache it was built with: build it again on this one.
    monkeypatch.setattr(AxesProxyHandler, "implementation", None)
    backend = caches["default"]
    assert isinstance(backend, ValkeyCache)
    try:
        backend.ping()
    except RedisError as error:
        pytest.fail(
            f"Valkey does not answer at VALKEY_URL ({type(error).__name__}). "
            f"{HOW_TO_START}",
        )
    yield backend
    for key in _run_keys(valkey_client, run_prefix):
        valkey_client.delete(key)


@pytest.fixture
def second_worker(cache_config: dict[str, Any]) -> Callable[[], ValkeyCache]:
    """Build another backend with its own connections, as another gunicorn process."""

    def build() -> ValkeyCache:
        # As django.core.cache builds one from a CACHES entry.
        params = dict(cache_config)
        params.pop("BACKEND")
        return ValkeyCache(params.pop("LOCATION"), params)

    return build


def _server_config(url: str) -> dict[str, Any]:
    """The CACHES entry the settings build for the database of the cache."""
    return build_valkey_cache(
        url=url,
        db=CACHE_DB,
        username=django_settings.VALKEY_USERNAME,
        password=django_settings.VALKEY_PASSWORD,
    )


def _run_keys(client: Redis, run_prefix: str) -> list[str]:
    return [key.decode() for key in client.scan_iter(match=f"{run_prefix}:*")]
