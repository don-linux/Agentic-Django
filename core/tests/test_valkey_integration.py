"""
What only a real Valkey shows: shared counters, TTLs, the broker connection.

Marker `valkey`: these need the dev-stack Valkey and fail, never skip, without it.
See docs/development.md#pytest.
"""

from __future__ import annotations

import time
import uuid
from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Any
from urllib.parse import urlsplit

import pytest
from allauth.account.models import EmailAddress
from django.conf import settings as django_settings
from django.core.cache import caches
from django.core.cache.backends.redis import RedisCache
from django.test import Client
from kombu.exceptions import OperationalError
from redis.exceptions import AuthenticationError
from redis.exceptions import NoPermissionError

from apps.users.selectors import user_get
from apps.users.services import user_update
from apps.users.tests.factories import UserFactory
from config import celery_app
from config.settings.base import build_broker_url
from core.cache import NAMESPACE_TTL
from core.cache_backend import ValkeyCache
from core.tests.credentials import PASSWORD
from core.tests.valkey_fixtures import CACHE_DB
from core.tests.valkey_fixtures import HOW_TO_START
from core.tests.valkey_fixtures import _run_keys

if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest_django.fixtures import Settings
    from redis import Redis

    from apps.users.models import User
    from conftest import CacheWrites
    from conftest import ThrottleRates

pytestmark = [pytest.mark.valkey, pytest.mark.django_db]

# Never 1 (the broker) of a server that may be in use, nor the cache's CACHE_DB.
BROKER_DB = 14
# A port of the same host where nothing listens.
CLOSED_PORT = 1
PROBE_TIMEOUT_SECONDS = 3
LOGIN_URL = "/_allauth/app/v1/auth/login"
ME_URL = "/api/v1/users/me/"
ONE_MINUTE = 60
# Seconds a slow run may take between a write and the TTL read.
TTL_SLACK = 5
# Any value: it comes back from the server through pickle.
NAMESPACE_VERSION = 123


def _ttl(client: Redis, backend: ValkeyCache, key: str) -> int:
    return client.ttl(backend.make_key(key))


def _close_to(ttl: int, seconds: int) -> bool:
    return seconds - TTL_SLACK <= ttl <= seconds


def _verified_user(email: str) -> User:
    user = UserFactory(email=email, password=PASSWORD)
    EmailAddress.objects.create(user=user, email=email, primary=True, verified=True)
    return user


def _login(email: str, password: str) -> Any:
    return Client().post(
        LOGIN_URL,
        {"email": email, "password": password},
        content_type="application/json",
    )


def test_round_trip_and_ttl(valkey: ValkeyCache, valkey_client: Redis) -> None:
    valkey.set("answer", {"value": 42}, 30)
    valkey.set("no-timeout", "kept", None)

    assert valkey.get("answer") == {"value": 42}
    assert 0 < _ttl(valkey_client, valkey, "answer") <= 30  # noqa: PLR2004
    # A key written without a timeout still expires: Valkey evicts only keys
    # that have a TTL, and the broker shares the server.
    assert 0 < _ttl(valkey_client, valkey, "no-timeout") <= valkey.max_ttl


def test_add_is_atomic_and_incr_keeps_the_ttl(
    valkey: ValkeyCache, valkey_client: Redis
) -> None:
    assert valkey.add("counter", 1, 60) is True
    assert valkey.add("counter", 5, 60) is False
    assert valkey.incr("counter") == 2  # noqa: PLR2004
    assert 0 < _ttl(valkey_client, valkey, "counter") <= 60  # noqa: PLR2004


# What ValkeyCache passes on to the server: a TTL on every write, and the key,
# value and timeout it was given.


@pytest.mark.parametrize("method", ["set", "add", "strict_set"])
def test_a_write_without_timeout_reaches_the_server_with_a_ttl(
    valkey: ValkeyCache,
    valkey_client: Redis,
    method: str,
) -> None:
    getattr(valkey, method)("key", "value", timeout=None)

    assert _close_to(_ttl(valkey_client, valkey, "key"), valkey.max_ttl)


def test_touch_without_timeout_never_makes_a_key_persistent(
    valkey: ValkeyCache, valkey_client: Redis
) -> None:
    valkey.set("key", "value", ONE_MINUTE)

    assert valkey.touch("key", timeout=None) is True
    assert _close_to(_ttl(valkey_client, valkey, "key"), valkey.max_ttl)


def test_set_many_without_timeout_sets_a_ttl(
    valkey: ValkeyCache, valkey_client: Redis
) -> None:
    assert valkey.set_many({"one": 1, "two": 2}, timeout=None) == []

    assert _close_to(_ttl(valkey_client, valkey, "one"), valkey.max_ttl)
    assert _close_to(_ttl(valkey_client, valkey, "two"), valkey.max_ttl)


def test_has_key_and_get_many_forward_the_key(valkey: ValkeyCache) -> None:
    valkey.set("key", "value", ONE_MINUTE)

    assert valkey.has_key("key") is True
    assert valkey.has_key("other") is False
    assert valkey.get_many(["key", "other"]) == {"key": "value"}


def test_set_many_and_touch_keep_an_explicit_timeout(
    valkey: ValkeyCache, valkey_client: Redis
) -> None:
    valkey.set_many({"one": 1}, timeout=ONE_MINUTE)
    valkey.set("two", 2, None)
    valkey.touch("two", timeout=ONE_MINUTE)

    assert _close_to(_ttl(valkey_client, valkey, "one"), ONE_MINUTE)
    assert _close_to(_ttl(valkey_client, valkey, "two"), ONE_MINUTE)


def test_strict_set_stores_the_given_value(
    valkey: ValkeyCache, valkey_client: Redis
) -> None:
    valkey.strict_set("ns:users.user", NAMESPACE_VERSION, ONE_MINUTE)

    assert valkey.get("ns:users.user") == NAMESPACE_VERSION
    assert _close_to(_ttl(valkey_client, valkey, "ns:users.user"), ONE_MINUTE)


def test_two_workers_share_one_cache(
    valkey: ValkeyCache,
    second_worker: Callable[[], ValkeyCache],
) -> None:
    valkey.set("written-by-one", "read-by-the-other", 30)

    assert second_worker().get("written-by-one") == "read-by-the-other"


def test_the_api_answers_429_from_the_shared_counter(
    valkey: ValkeyCache,
    throttle_rates: ThrottleRates,
    user_client: Client,
) -> None:
    throttle_rates({"anon": "100/min", "user": "2/min"})

    responses = [user_client.get(ME_URL) for _ in range(3)]

    assert [response.status_code for response in responses] == [
        HTTPStatus.OK,
        HTTPStatus.OK,
        HTTPStatus.TOO_MANY_REQUESTS,
    ]
    assert responses[-1].json()["code"] == "throttled"


def test_every_key_the_application_writes_has_a_ttl(
    valkey: ValkeyCache,
    valkey_client: Redis,
    run_prefix: str,
    cache_writes: CacheWrites,
    user_client: Client,
) -> None:
    owner = _verified_user("ttl@example.com")
    # A throttled request, a failed and a successful login (rate limits and a
    # cached session), and a cached read with its namespace version.
    assert user_client.get(ME_URL).status_code == HTTPStatus.OK
    wrong = _login(owner.email, "Wrong-H0rse-Battery")
    assert wrong.status_code == HTTPStatus.BAD_REQUEST
    assert _login(owner.email, PASSWORD).status_code == HTTPStatus.OK
    with cache_writes():
        user_get(public_id=owner.public_id)

    ttls = {key: valkey_client.ttl(key) for key in _run_keys(valkey_client, run_prefix)}

    for family in (
        "throttle_user",
        "allauth:rl:",
        "sessions.cached_db",
        "ns:users.user",
        "users.user:v",
    ):
        assert any(family in key for key in ttls), f"no {family} key was written"
    # volatile-lru could evict none of them otherwise.
    assert [key for key, ttl in ttls.items() if ttl <= 0] == []


def test_a_cached_selector_hits_and_invalidates_on_valkey(
    valkey: ValkeyCache,
    valkey_client: Redis,
    cache_writes: CacheWrites,
    django_assert_num_queries,
    user: User,
) -> None:
    with cache_writes():
        user_get(public_id=user.public_id)

    # The pickled user comes back from Valkey, without its password hash.
    with django_assert_num_queries(0):
        cached = user_get(public_id=user.public_id)
    assert cached == user
    assert "password" in cached.get_deferred_fields()
    assert 0 < _ttl(valkey_client, valkey, "ns:users.user") <= NAMESPACE_TTL

    with cache_writes():
        user_update(user=user, name="Ada Lovelace")
    with django_assert_num_queries(1):
        assert user_get(public_id=user.public_id).name == "Ada Lovelace"


def test_readiness_is_ok_with_valkey_up(valkey: ValkeyCache) -> None:
    response = Client().get("/health/")

    assert response.status_code == HTTPStatus.OK
    assert response.content == b"ok\n"


def test_ping_reaches_valkey_and_writes_nothing(valkey: ValkeyCache) -> None:
    valkey.ping()

    assert valkey.has_key("health:ping") is False


def test_ping_sets_the_cache_timeout_on_the_versioned_key(
    valkey: ValkeyCache,
    valkey_client: Redis,
    cache_config: dict[str, Any],
) -> None:
    # The physical key is <KEY_PREFIX>:<VERSION>:health:ping, and the probe is
    # one EXPIRE with the TIMEOUT of CACHES: it shows on a key that already exists.
    key = valkey.make_key("health:ping")
    assert key == f"{cache_config['KEY_PREFIX']}:{cache_config['VERSION']}:health:ping"
    valkey_client.set(key, b"left by hand")

    valkey.ping()

    assert (
        cache_config["TIMEOUT"] - 1
        <= _ttl(valkey_client, valkey, "health:ping")
        <= cache_config["TIMEOUT"]
    )


def test_readiness_is_503_when_the_valkey_user_cannot_write(
    valkey: ValkeyCache,
    valkey_client: Redis,
    settings: Settings,
    cache_config: dict[str, Any],
    run_prefix: str,
) -> None:
    # EXPIRE is a write command: a user that may only read connects, selects the
    # database and reads, but the probe is refused (NOPERM) and /health/ is 503.
    username, password = f"{run_prefix}-read-only", uuid.uuid4().hex
    admin = valkey_client
    admin.acl_setuser(
        username,
        enabled=True,
        passwords=[f"+{password}"],
        keys=["*"],
        categories=["+@read", "+@connection"],
        commands=["+select"],
    )
    try:
        options = {
            **cache_config["OPTIONS"],
            "username": username,
            "password": password,
        }
        settings.CACHES = {"default": {**cache_config, "OPTIONS": options}}
        backend = caches["default"]
        assert isinstance(backend, ValkeyCache)
        # Django's own has_key, without ValkeyCache's fail-open: an EXISTS as
        # that user, which raises if the user cannot connect, select or read.
        assert RedisCache.has_key(backend, "anything") is False

        with pytest.raises(NoPermissionError):
            backend.ping()
        response = Client().get("/health/")
    finally:
        admin.acl_deluser(username)

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.content == b"cache unavailable\n"


def test_readiness_is_503_when_valkey_refuses_the_credentials(
    settings: Settings,
    cache_config: dict[str, Any],
) -> None:
    # A user Valkey does not know: WRONGPASS whether or not the server has a password.
    options = {**cache_config["OPTIONS"], "username": "nobody", "password": "wrong"}
    settings.CACHES = {"default": {**cache_config, "OPTIONS": options}}
    backend = caches["default"]
    assert isinstance(backend, ValkeyCache)

    with pytest.raises(AuthenticationError):
        backend.ping()
    response = Client().get("/health/")

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.content == b"cache unavailable\n"


def test_readiness_is_503_quickly_when_the_port_is_closed(
    settings: Settings,
    cache_config: dict[str, Any],
) -> None:
    # The real host, resolved as in production, but a port that refuses.
    location = urlsplit(cache_config["LOCATION"])
    dead = f"{location.scheme}://{location.hostname}:{CLOSED_PORT}/{CACHE_DB}"
    settings.CACHES = {"default": {**cache_config, "LOCATION": dead}}
    started = time.monotonic()

    response = Client().get("/health/")

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.content == b"cache unavailable\n"
    assert time.monotonic() - started < PROBE_TIMEOUT_SECONDS


def test_celery_connects_to_valkey_as_its_broker(server_url: str) -> None:
    broker_url = build_broker_url(
        url=server_url,
        db=BROKER_DB,
        username=django_settings.VALKEY_USERNAME,
        password=django_settings.VALKEY_PASSWORD,
    )

    # The tests' own broker is in memory: this one is given explicitly.
    with celery_app.connection_for_write(broker_url) as connection:
        try:
            connection.ensure_connection(max_retries=1, interval_start=0, timeout=5)
        except OperationalError as error:
            pytest.fail(
                f"Valkey does not accept the broker connection at VALKEY_URL "
                f"({type(error).__name__}). {HOW_TO_START}",
            )

        assert connection.connected
        assert connection.transport.driver_type == "redis"
