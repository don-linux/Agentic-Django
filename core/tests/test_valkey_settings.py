from __future__ import annotations

import subprocess

import pytest
from axes.checks import axes_cache_check
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from config.settings.base import CACHE_KEY_PREFIX
from config.settings.base import build_valkey_cache
from config.settings.base import positive_seconds
from config.settings.base import valkey_url
from core.tests.production_settings import load_settings
from core.tests.production_settings import run_python

# Long enough for the production guard (config/settings/guards.py).
VALKEY_SECRET = "tests-only-valkey-secret-never-used-anywhere"  # noqa: S105
VALKEY_BACKEND = "core.cache_backend.ValkeyCache"
SERVER = "redis://cache.tests.invalid:6379"
ONE_SECOND = 1.0
QUARTER_SECOND = 0.25
TWO_SECONDS = 2.0
HEALTH_CHECK_SECONDS = 30
ONE_MINUTE = 60


def _cache(*, username: str = "", password: str = VALKEY_SECRET) -> dict[str, object]:
    return build_valkey_cache(url=SERVER, db=0, username=username, password=password)


@pytest.mark.parametrize(
    ("base_url", "db", "expected"),
    [
        ("redis://valkey:6379", 0, "redis://valkey:6379/0"),
        ("redis://valkey:6379/", 1, "redis://valkey:6379/1"),
        ("redis://valkey:6379/7", 1, "redis://valkey:6379/1"),
        (
            "rediss://cache.tests.invalid:6380?ssl_cert_reqs=required",
            0,
            "rediss://cache.tests.invalid:6380/0?ssl_cert_reqs=required",
        ),
    ],
)
def test_valkey_url_sets_the_database_and_keeps_scheme_and_query(
    base_url: str,
    db: int,
    expected: str,
) -> None:
    assert valkey_url(base_url, db) == expected


@pytest.mark.parametrize(
    "base_url",
    [
        f"redis://:{VALKEY_SECRET}@valkey:6379",
        f"redis://cache-user:{VALKEY_SECRET}@valkey:6379",
        "redis://cache-user@valkey:6379",
    ],
)
def test_valkey_url_refuses_credentials_without_echoing_them(base_url: str) -> None:
    with pytest.raises(ImproperlyConfigured, match="VALKEY_PASSWORD") as excinfo:
        valkey_url(base_url, 0)

    assert VALKEY_SECRET not in str(excinfo.value)
    assert "cache-user" not in str(excinfo.value)


@pytest.mark.parametrize(
    "base_url",
    [
        "valkey://valkey:6379",
        "valkeys://valkey:6379",
        "http://valkey:6379",
        "valkey:6379",
        "unix:///run/valkey.sock",
    ],
)
def test_valkey_url_refuses_schemes_the_clients_do_not_speak(base_url: str) -> None:
    # redis-py and Celery's transport only know redis:// and rediss://; the
    # server is Valkey, but "fixing" the scheme to valkey:// breaks both.
    with pytest.raises(ImproperlyConfigured, match="redis://"):
        valkey_url(base_url, 0)


def test_build_valkey_cache_keeps_the_password_out_of_the_location() -> None:
    config = _cache()
    options = config["OPTIONS"]

    assert config["BACKEND"] == VALKEY_BACKEND
    assert config["LOCATION"] == f"{SERVER}/0"
    assert isinstance(options, dict)
    assert options["password"] == VALKEY_SECRET
    assert "username" not in options


def test_build_valkey_cache_sends_credentials_only_when_set() -> None:
    with_user = _cache(username="cache-user")["OPTIONS"]
    anonymous = _cache(password="")["OPTIONS"]

    assert isinstance(with_user, dict)
    assert isinstance(anonymous, dict)
    assert with_user["username"] == "cache-user"
    assert "username" not in anonymous
    assert "password" not in anonymous


def test_build_valkey_cache_fails_fast_and_never_retries() -> None:
    options = _cache()["OPTIONS"]

    assert isinstance(options, dict)
    assert options["socket_connect_timeout"] == ONE_SECOND
    assert options["socket_timeout"] == ONE_SECOND
    assert options["retry_on_timeout"] is False
    assert options["health_check_interval"] == HEALTH_CHECK_SECONDS


def test_every_cache_entry_gets_a_ttl() -> None:
    config = _cache()

    # Valkey only evicts keys that have a TTL: neither default may be None or 0.
    for name in ("TIMEOUT", "MAX_TTL"):
        value = config[name]
        assert isinstance(value, int)
        assert value > 0
    assert config["KEY_PREFIX"] == CACHE_KEY_PREFIX
    assert CACHE_KEY_PREFIX
    assert config["VERSION"] == 1


@pytest.mark.parametrize("value", [0, -1])
def test_positive_seconds_refuses_zero_or_less(value: int) -> None:
    with pytest.raises(ImproperlyConfigured, match="DJANGO_CACHE_TIMEOUT"):
        positive_seconds("DJANGO_CACHE_TIMEOUT", value)


def test_positive_seconds_returns_a_positive_value() -> None:
    assert positive_seconds("VALKEY_MAX_TTL", ONE_MINUTE) == ONE_MINUTE


@pytest.mark.parametrize(
    "module",
    ["config.settings.base", "config.settings.local", "config.settings.production"],
)
def test_every_environment_but_tests_caches_in_valkey(module: str) -> None:
    caches = load_settings(
        module,
        "CACHES",
        env={"VALKEY_URL": SERVER, "VALKEY_PASSWORD": VALKEY_SECRET},
    )["CACHES"]

    assert set(caches) == {"default"}
    assert caches["default"]["BACKEND"] == VALKEY_BACKEND
    assert caches["default"]["LOCATION"] == f"{SERVER}/0"
    assert caches["default"]["OPTIONS"]["password"] == VALKEY_SECRET


def test_timeouts_and_database_come_from_the_environment() -> None:
    default = load_settings(
        "config.settings.base",
        "CACHES",
        env={
            "VALKEY_CONNECT_TIMEOUT": "0.25",
            "VALKEY_SOCKET_TIMEOUT": "2",
            "VALKEY_CACHE_DB": "3",
        },
    )["CACHES"]["default"]

    assert default["OPTIONS"]["socket_connect_timeout"] == QUARTER_SECOND
    assert default["OPTIONS"]["socket_timeout"] == TWO_SECONDS
    assert default["LOCATION"].endswith("/3")


def test_a_url_with_credentials_stops_the_settings_import() -> None:
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        load_settings(
            "config.settings.base",
            "CACHES",
            env={"VALKEY_URL": f"redis://:{VALKEY_SECRET}@valkey:6379"},
        )

    assert "ImproperlyConfigured" in excinfo.value.stderr
    assert "VALKEY_PASSWORD" in excinfo.value.stderr
    assert VALKEY_SECRET not in excinfo.value.stderr


def test_local_settings_do_not_silence_the_axes_cache_check() -> None:
    result = run_python(
        "import config.settings.local as local\n"
        "print(getattr(local, 'SILENCED_SYSTEM_CHECKS', []))\n",
    )

    assert "axes.W001" not in result.stdout


def test_axes_accepts_valkey_as_a_shared_cache() -> None:
    # axes.W001 fires for per-process caches; it must stay quiet on Valkey.
    with override_settings(CACHES={"default": _cache()}):
        assert axes_cache_check(None) == []


def _key_prefix(value: str | None) -> str:
    caches = load_settings(
        "config.settings.base", "CACHES", env={"DJANGO_CACHE_KEY_PREFIX": value}
    )["CACHES"]
    return caches["default"]["KEY_PREFIX"]


def test_an_empty_cache_key_prefix_falls_back_to_the_default() -> None:
    # The default is the project slug, which the personalize skill rewrites.
    default = _key_prefix(None)

    assert default
    assert _key_prefix("") == default
    assert _key_prefix("acme") == "acme"
