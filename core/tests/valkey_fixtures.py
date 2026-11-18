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
def cache_config(server_url: str, run_prefix: str) -> dict[str, Any]:
    config = build_valkey_cache(
        url=server_url,
        db=CACHE_DB,
        username=django_settings.VALKEY_USERNAME,
        password=django_settings.VALKEY_PASSWORD,
    )
    return {**config, "KEY_PREFIX": run_prefix}


@pytest.fixture
def valkey(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    cache_config: dict[str, Any],
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
    client = _client(backend)
    for key in _run_keys(backend, run_prefix):
        client.delete(key)
    _disconnect(backend)


@pytest.fixture
def second_worker(cache_config: dict[str, Any]) -> Iterator[Callable[[], ValkeyCache]]:
    """Build another backend with its own connections, as another gunicorn process."""
    backends: list[ValkeyCache] = []

    def build() -> ValkeyCache:
        # As django.core.cache builds one from a CACHES entry.
        params = dict(cache_config)
        params.pop("BACKEND")
        backends.append(ValkeyCache(params.pop("LOCATION"), params))
        return backends[-1]

    yield build
    for backend in backends:
        _disconnect(backend)


def _client(backend: ValkeyCache) -> Any:
    # The redis-py client behind the backend: the cache API has no TTL or SCAN.
    return backend._cache.get_client(write=True)  # noqa: SLF001


def _disconnect(backend: ValkeyCache) -> None:
    # One server, so one pool: close its sockets instead of leaving them to the
    # garbage collector.
    _client(backend).connection_pool.disconnect()


def _run_keys(backend: ValkeyCache, run_prefix: str) -> list[str]:
    client = _client(backend)
    return [key.decode() for key in client.scan_iter(match=f"{run_prefix}:*")]
