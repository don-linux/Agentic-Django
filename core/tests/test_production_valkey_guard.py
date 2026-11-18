from __future__ import annotations

import subprocess
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.settings.guards import MIN_VALKEY_PASSWORD_LENGTH
from config.settings.guards import assert_production_valkey
from core.tests.production_settings import PRODUCTION_ENV
from core.tests.production_settings import load_production_settings

STRONG = "tests-only-valkey-guard-" + "Kq8vN2xR7mPz4wLs9TjB6yHc"
VALID: dict[str, Any] = {
    "url": "redis://valkey:6379",
    "password": STRONG,
    "cache_db": 0,
    "broker_db": 1,
}
TLS_URL = "rediss://cache.tests.invalid:6380"


def _assert_rejected(match: str, **overrides: Any) -> None:
    with pytest.raises(ImproperlyConfigured, match=match) as excinfo:
        assert_production_valkey(**{**VALID, **overrides})
    for value in overrides.values():
        if isinstance(value, str) and value:
            # Messages name the variable but never echo its value.
            assert value not in str(excinfo.value)


@pytest.mark.parametrize(
    "url",
    [
        "redis://valkey:6379",
        f"{TLS_URL}?ssl_cert_reqs=required",
        f"{TLS_URL}?ssl_cert_reqs=required&socket_timeout=5",
        f"{TLS_URL}?socket_timeout=5&ssl_cert_reqs=required",
        # The host name check stays on.
        f"{TLS_URL}?ssl_cert_reqs=required&ssl_check_hostname=true",
        f"{TLS_URL}?ssl_cert_reqs=required&ssl_check_hostname=1",
    ],
)
def test_valid_settings_pass(url: str) -> None:
    assert_production_valkey(**{**VALID, "url": url})


@pytest.mark.parametrize(
    "query",
    [
        # Nothing asked: the Celery broker would connect without verifying.
        "",
        "?socket_timeout=5",
        "?ssl_cert_reqs=",
        "?ssl_cert_reqs=none",
        "?ssl_cert_reqs=optional",
        # Spellings only one of the two clients accepts.
        "?ssl_cert_reqs=CERT_REQUIRED",
        "?ssl_cert_reqs=Required",
        "?SSL_CERT_REQS=required",
        # The cache reads the first value and the broker the last.
        "?ssl_cert_reqs=required&ssl_cert_reqs=none",
        "?ssl_cert_reqs=none&ssl_cert_reqs=required",
    ],
)
def test_tls_without_certificate_verification_is_rejected(query: str) -> None:
    _assert_rejected(
        "VALKEY_URL uses rediss:// without ssl_cert_reqs=required",
        url=TLS_URL + query,
    )


@pytest.mark.parametrize(
    "host_name_check",
    [
        "ssl_check_hostname=false",
        "ssl_check_hostname=False",
        "ssl_check_hostname=FALSE",
        # The cache reads these as "off" too; the broker does not.
        "ssl_check_hostname=0",
        "ssl_check_hostname=f",
        "ssl_check_hostname=N",
        "ssl_check_hostname=no",
        # The cache reads the first value and the broker the last.
        "ssl_check_hostname=true&ssl_check_hostname=false",
        "ssl_check_hostname=false&ssl_check_hostname=true",
    ],
)
@pytest.mark.parametrize(
    "template", ["?ssl_cert_reqs=required&{}", "?{}&ssl_cert_reqs=required"]
)
def test_tls_that_skips_the_host_name_check_is_rejected(
    template: str,
    host_name_check: str,
) -> None:
    # The certificate is verified, but for any server: a valid one for another
    # host name would pass.
    url = TLS_URL + template.format(host_name_check)

    with pytest.raises(ImproperlyConfigured, match="ssl_check_hostname") as excinfo:
        assert_production_valkey(**{**VALID, "url": url})

    message = str(excinfo.value)
    assert url not in message
    assert "cache.tests.invalid" not in message
    assert STRONG not in message


def test_the_tls_message_shows_neither_the_url_nor_the_password() -> None:
    with pytest.raises(ImproperlyConfigured) as excinfo:
        assert_production_valkey(**{**VALID, "url": TLS_URL})

    message = str(excinfo.value)
    assert "?ssl_cert_reqs=required" in message
    assert STRONG not in message
    assert "cache.tests.invalid" not in message


@pytest.mark.parametrize(
    "url",
    ["valkey://valkey:6379", "http://valkey:6379", "valkey:6379", "memory://"],
)
def test_other_schemes_are_rejected(url: str) -> None:
    _assert_rejected("VALKEY_URL must start with redis://", url=url)


@pytest.mark.parametrize(
    "url",
    [f"redis://:{STRONG}@valkey:6379", f"redis://cache-user:{STRONG}@valkey:6379"],
)
def test_credentials_in_the_url_are_rejected(url: str) -> None:
    _assert_rejected("VALKEY_URL must not contain credentials", url=url)


def test_missing_password_is_rejected() -> None:
    _assert_rejected("VALKEY_PASSWORD is required", password="")


@pytest.mark.parametrize(
    "password",
    [
        "change-me",
        "change-me-" + "x" * 40,
        "CHANGE-ME-CHANGE-ME-CHANGE-ME-XX",
        "changeme" + "x" * 24,
        "change_me" + "x" * 24,
    ],
)
def test_example_password_is_rejected(password: str) -> None:
    _assert_rejected("VALKEY_PASSWORD still contains", password=password)


def test_short_password_is_rejected() -> None:
    _assert_rejected(
        "VALKEY_PASSWORD must have at least",
        password="s" * (MIN_VALKEY_PASSWORD_LENGTH - 1),
    )


@pytest.mark.parametrize("char", ['"', "\\", "\n", "\r"])
def test_password_that_would_break_valkey_conf_is_rejected(char: str) -> None:
    # compose.yaml writes it as requirepass "<password>" in valkey.conf.
    _assert_rejected(
        "VALKEY_PASSWORD must not contain quotes",
        password="s" * MIN_VALKEY_PASSWORD_LENGTH + char + 'requirepass ""',
    )


def test_production_refuses_a_quote_in_the_valkey_password() -> None:
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        load_production_settings(
            "CACHES",
            env={"VALKEY_PASSWORD": "q" * MIN_VALKEY_PASSWORD_LENGTH + '"x'},
        )
    assert "VALKEY_PASSWORD must not contain quotes" in excinfo.value.stderr
    assert "q" * MIN_VALKEY_PASSWORD_LENGTH not in excinfo.value.stderr


def test_the_cache_and_the_broker_cannot_share_a_database() -> None:
    _assert_rejected("VALKEY_CACHE_DB and VALKEY_BROKER_DB", cache_db=1, broker_db=1)


def test_all_problems_are_reported_together() -> None:
    with pytest.raises(ImproperlyConfigured) as excinfo:
        assert_production_valkey(
            url="valkey://valkey:6379",
            password="",
            cache_db=0,
            broker_db=0,
        )

    message = str(excinfo.value)
    assert "VALKEY_URL" in message
    assert "VALKEY_PASSWORD" in message
    assert "VALKEY_BROKER_DB" in message


@pytest.mark.parametrize("variable", ["VALKEY_URL", "VALKEY_PASSWORD"])
def test_production_does_not_start_without_valkey_settings(variable: str) -> None:
    # No silent fallback to localhost or to an empty password.
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        load_production_settings("CACHES", env={variable: None})

    assert "ImproperlyConfigured" in excinfo.value.stderr
    assert variable in excinfo.value.stderr


def test_production_module_calls_the_valkey_guard() -> None:
    weak = "weak-valkey-password"

    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        load_production_settings("CACHES", env={"VALKEY_PASSWORD": weak})

    assert "ImproperlyConfigured" in excinfo.value.stderr
    assert "VALKEY_PASSWORD must have at least" in excinfo.value.stderr
    assert weak not in excinfo.value.stderr


def test_production_refuses_a_shared_database() -> None:
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        load_production_settings("CACHES", env={"VALKEY_BROKER_DB": "0"})

    assert "VALKEY_CACHE_DB and VALKEY_BROKER_DB" in excinfo.value.stderr


def test_production_refuses_tls_to_valkey_without_certificate_verification() -> None:
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        load_production_settings(
            "CACHES",
            env={"VALKEY_URL": "rediss://valkey.tests.invalid:6380"},
        )

    stderr = excinfo.value.stderr
    assert "ImproperlyConfigured" in stderr
    assert "VALKEY_URL uses rediss:// without ssl_cert_reqs=required" in stderr
    # The broker URL built from it embeds the password: neither is printed.
    assert PRODUCTION_ENV["VALKEY_PASSWORD"] not in stderr
    assert "valkey.tests.invalid" not in stderr


def test_production_starts_with_verified_tls_to_valkey() -> None:
    values = load_production_settings(
        "CACHES",
        "CELERY_BROKER_URL",
        env={"VALKEY_URL": "rediss://valkey.tests.invalid:6380?ssl_cert_reqs=required"},
    )

    assert values["CACHES"]["default"]["LOCATION"] == (
        "rediss://valkey.tests.invalid:6380/0?ssl_cert_reqs=required"
    )
    assert values["CELERY_BROKER_URL"].startswith("rediss://")
    assert values["CELERY_BROKER_URL"].endswith(
        "@valkey.tests.invalid:6380/1?ssl_cert_reqs=required",
    )


def test_production_refuses_tls_to_valkey_that_skips_the_host_name_check() -> None:
    url = (
        "rediss://valkey.tests.invalid:6380"
        "?ssl_cert_reqs=required&ssl_check_hostname=False"
    )

    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        load_production_settings("CACHES", env={"VALKEY_URL": url})

    stderr = excinfo.value.stderr
    assert "ImproperlyConfigured" in stderr
    assert "turns the host name check off with ssl_check_hostname" in stderr
    assert PRODUCTION_ENV["VALKEY_PASSWORD"] not in stderr
    assert "valkey.tests.invalid" not in stderr
