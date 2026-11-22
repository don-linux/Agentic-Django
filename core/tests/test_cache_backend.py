from __future__ import annotations

import logging
import time
from http import HTTPStatus
from typing import TYPE_CHECKING
from unittest import mock

import pytest
from axes.handlers.proxy import AxesProxyHandler
from django.contrib.sessions.backends.base import UpdateError
from django.contrib.sessions.backends.cached_db import KEY_PREFIX as SESSION_PREFIX
from django.contrib.sessions.models import Session
from django.core.cache.backends.redis import RedisCache
from django.test import Client
from redis.exceptions import ConnectionError as ValkeyConnectionError
from redis.exceptions import ResponseError
from redis.exceptions import TimeoutError as ValkeyTimeoutError

from core import cache_backend
from core.cache_backend import DEFAULT_MAX_TTL
from core.cache_backend import WARNING_INTERVAL
from core.cache_backend import OutageLog
from core.cache_backend import ValkeyCache
from core.tests.cache_helpers import LOCATION
from core.tests.cache_helpers import MAX_TTL
from core.tests.cache_helpers import ONE_MINUTE
from core.tests.cache_helpers import SESSION_URL
from core.tests.cache_helpers import _backend
from core.tests.cache_helpers import _login
from core.tests.cache_helpers import _root_cause

if TYPE_CHECKING:
    from pytest_django.fixtures import Settings

DEAD_OPTIONS = {"socket_connect_timeout": 0.05, "socket_timeout": 0.05}
DEFAULT_TTL = 300
THIRTY_DAYS = 30 * 24 * 60 * 60
ONE_SECOND = 1.0
ME_URL = "/api/v1/users/me/"
CACHED_DB = "django.contrib.sessions.backends.cached_db"


@pytest.fixture(autouse=True)
def _silent_outage_log(monkeypatch: pytest.MonkeyPatch) -> None:
    # The log is rate limited per process: every test starts with a fresh one.
    monkeypatch.setattr(cache_backend, "outage_log", OutageLog(WARNING_INTERVAL))


@pytest.fixture
def dead() -> ValkeyCache:
    """A backend whose server is a closed port."""
    return _backend(OPTIONS=DEAD_OPTIONS)


@pytest.fixture
def valkey_outage(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the default cache at a closed port and use cached_db sessions."""
    settings.CACHES = {
        "default": {
            "BACKEND": "core.cache_backend.ValkeyCache",
            "LOCATION": LOCATION,
            "KEY_PREFIX": "tests",
            "OPTIONS": DEAD_OPTIONS,
        },
    }
    settings.SESSION_ENGINE = CACHED_DB
    # django-axes keeps the cache it was built with: build it again on this one.
    monkeypatch.setattr(AxesProxyHandler, "implementation", None)


def test_a_key_without_timeout_gets_the_maximum_ttl() -> None:
    backend = _backend(TIMEOUT=DEFAULT_TTL, MAX_TTL=MAX_TTL)

    assert backend.get_backend_timeout(None) == MAX_TTL


def test_explicit_and_default_timeouts_are_kept() -> None:
    backend = _backend(TIMEOUT=DEFAULT_TTL, MAX_TTL=MAX_TTL)

    assert backend.get_backend_timeout() == DEFAULT_TTL
    assert backend.get_backend_timeout(ONE_MINUTE) == ONE_MINUTE
    # Zero or less still means "expire now", as in Django's backend.
    assert backend.get_backend_timeout(0) == 0
    assert backend.get_backend_timeout(-ONE_MINUTE) == 0


def test_a_default_timeout_of_none_is_capped_too() -> None:
    backend = _backend(TIMEOUT=None, MAX_TTL=MAX_TTL)

    assert backend.get_backend_timeout() == MAX_TTL


def test_max_ttl_defaults_to_thirty_days() -> None:
    assert _backend().max_ttl == DEFAULT_MAX_TTL == THIRTY_DAYS


def test_reads_fail_open_as_on_an_empty_cache(dead: ValkeyCache) -> None:
    assert dead.get("key") is None
    assert dead.get("key", "fallback") == "fallback"
    assert dead.get_many(["one", "two"]) == {}
    assert dead.has_key("key") is False
    assert "key" not in dead
    assert dead.get_or_set("key", "computed") == "computed"


def test_writes_fail_open_without_raising(dead: ValkeyCache) -> None:
    dead.set("key", "value")

    # add() succeeds as on an empty cache; set_many() names what was not stored.
    assert dead.add("key", "value") is True
    assert dead.touch("key") is False
    assert dead.set_many({"one": 1, "two": 2}) == ["one", "two"]


@pytest.mark.parametrize("method", ["incr", "decr"])
def test_counters_find_no_key(dead: ValkeyCache, method: str) -> None:
    with pytest.raises(ValueError, match="not found"):
        getattr(dead, method)("key")


def test_a_timeout_fails_open_and_other_errors_still_raise() -> None:
    backend = _backend()
    # Django's RedisCache.get, which ValkeyCache.get wraps, raises what the
    # server answered.
    timeout = ValkeyTimeoutError("Timeout reading from socket")

    with mock.patch.object(RedisCache, "get", side_effect=timeout):
        assert backend.get("key", "fallback") == "fallback"

    with (
        mock.patch.object(RedisCache, "get", side_effect=ResponseError("WRONGTYPE")),
        pytest.raises(ResponseError),
    ):
        backend.get("key")


def test_a_dead_valkey_costs_one_failed_connection_per_call(
    dead: ValkeyCache,
) -> None:
    started = time.monotonic()

    for _ in range(20):
        dead.get("key")

    # No hidden retry with backoff: twenty calls stay far below one second.
    assert time.monotonic() - started < ONE_SECOND


def test_an_outage_is_logged_once_per_interval(
    dead: ValkeyCache,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [0.0]
    monkeypatch.setattr(
        cache_backend,
        "outage_log",
        OutageLog(WARNING_INTERVAL, clock=lambda: now[0]),
    )

    with caplog.at_level(logging.WARNING, logger="core.cache"):
        dead.get("key")
        dead.set("key", "value")
        dead.has_key("key")
        first_burst = len(caplog.records)
        now[0] += WARNING_INTERVAL
        dead.get("key")

    assert first_burst == 1
    assert [record.name for record in caplog.records] == ["core.cache"] * 2
    message = caplog.records[0].getMessage()
    assert "cache get failed open" in message
    assert "ConnectionError" in message


@pytest.mark.parametrize(
    "key",
    ["users.user:v1:42", "ns:users.user", f"{SESSION_PREFIX}0123456789abcdef"],
)
def test_deletes_raise_while_valkey_is_down(dead: ValkeyCache, key: str) -> None:
    # A value or a session that could not be removed must not look removed.
    with pytest.raises(ValkeyConnectionError):
        dead.delete(key)


def test_delete_many_and_clear_raise_too(dead: ValkeyCache) -> None:
    with pytest.raises(ValkeyConnectionError):
        dead.delete_many(["one", "two"])
    with pytest.raises(ValkeyConnectionError):
        dead.clear()


@pytest.mark.parametrize(
    "key",
    [
        "allauth:rl:allauth.account:login:30/60/ip:ip:127.0.0.1:lock",
        "allauth:rl:allauth.account:login_failed:5/300/key:0123abcd",
        "axes-0123456789abcdef0123456789abcdef",
    ],
)
def test_rate_limit_bookkeeping_deletes_fail_open(
    dead: ValkeyCache,
    key: str,
) -> None:
    # allauth and django-axes delete these keys on every login.
    assert dead.delete(key) is False


def test_ping_is_django_s_touch_of_the_reserved_key() -> None:
    # Django's cache API has no ping. RedisCache.touch, not the fail-open
    # ValkeyCache.touch, with the default timeout (the TIMEOUT of CACHES), on the
    # key docs/ARCHITECTURE.md reserves for the probe.
    backend = _backend()
    with mock.patch.object(RedisCache, "touch", autospec=True) as touch:
        backend.ping()

    touch.assert_called_once_with(backend, "health:ping")


def test_strict_calls_never_fail_open(dead: ValkeyCache) -> None:
    with pytest.raises(ValkeyConnectionError):
        dead.ping()
    with pytest.raises(ValkeyConnectionError):
        dead.strict_set("ns:users.user", 1, ONE_MINUTE)


@pytest.mark.django_db
def test_a_throttled_request_is_served_during_an_outage(
    user_client: Client,
    caplog: pytest.LogCaptureFixture,
    request: pytest.FixtureRequest,
) -> None:
    # user_client logs in with a saved session: start the outage after it.
    request.getfixturevalue("valkey_outage")
    # An authenticated request: Ninja runs its throttles after the permissions.
    with caplog.at_level(logging.WARNING, logger="core.cache"):
        response = user_client.get(ME_URL)

    assert response.status_code == HTTPStatus.OK
    assert [record.name for record in caplog.records] == ["core.cache"]


@pytest.mark.django_db
@pytest.mark.usefixtures("valkey_outage")
def test_login_and_the_session_work_during_an_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_sleep(_seconds: float) -> None:
        message = "allauth waited for its rate-limit lock: add() did not fail open"
        raise AssertionError(message)

    # allauth sleeps (time.sleep) between attempts to take its lock, then
    # answers 429.
    monkeypatch.setattr(time, "sleep", no_sleep)

    token = _login()
    me = Client().get(ME_URL, HTTP_X_SESSION_TOKEN=token)

    assert me.status_code == HTTPStatus.OK


@pytest.mark.django_db
@pytest.mark.usefixtures("valkey_outage")
def test_logout_fails_loudly_during_an_outage() -> None:
    token = _login()

    # The cached copy of the session cannot be removed, so the logout must not
    # look done: the request fails (a 500 in production) and the client retries.
    # allauth saves the session while the error unwinds, which raises UpdateError
    # on top of it because Django had already deleted the row.
    with pytest.raises(UpdateError) as excinfo:
        Client().delete(SESSION_URL, HTTP_X_SESSION_TOKEN=token)

    assert _root_cause(excinfo.value, ValkeyConnectionError)
    assert not Session.objects.exists()
