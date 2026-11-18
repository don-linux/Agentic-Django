from __future__ import annotations

import subprocess

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.settings.guards import assert_production_celery
from core.tests.production_settings import load_production_settings
from core.tests.production_settings import run_python

BROKER_SECRET = "tests-only-broker-secret-never-used-anywhere"  # noqa: S105
BROKER_URL = f"redis://:{BROKER_SECRET}@valkey:6379/1"
TLS_BROKER_URL = f"rediss://:{BROKER_SECRET}@cache.tests.invalid:6380/1"


@pytest.mark.parametrize(
    "broker_url",
    [BROKER_URL, f"{TLS_BROKER_URL}?ssl_cert_reqs=required"],
)
def test_a_valkey_broker_without_eager_mode_passes(broker_url: str) -> None:
    assert_production_celery(always_eager=False, broker_url=broker_url)


@pytest.mark.parametrize(
    "query",
    [
        "",
        "?ssl_cert_reqs=none",
        "?ssl_cert_reqs=optional",
        "?ssl_cert_reqs=CERT_REQUIRED",
        "?ssl_cert_reqs=required&ssl_cert_reqs=none",
    ],
)
def test_a_tls_broker_without_certificate_verification_is_rejected(query: str) -> None:
    # kombu connects with verification off when the URL does not ask for it.
    with pytest.raises(ImproperlyConfigured, match="ssl_cert_reqs=required") as excinfo:
        assert_production_celery(always_eager=False, broker_url=TLS_BROKER_URL + query)

    # The URL embeds the Valkey password: the message shows neither.
    assert BROKER_SECRET not in str(excinfo.value)
    assert "cache.tests.invalid" not in str(excinfo.value)


@pytest.mark.parametrize("value", ["false", "False", "FALSE", "0", "no"])
def test_a_tls_broker_that_skips_the_host_name_check_is_rejected(value: str) -> None:
    broker_url = f"{TLS_BROKER_URL}?ssl_cert_reqs=required&ssl_check_hostname={value}"

    with pytest.raises(ImproperlyConfigured, match="ssl_check_hostname") as excinfo:
        assert_production_celery(always_eager=False, broker_url=broker_url)

    message = str(excinfo.value)
    assert broker_url not in message
    assert BROKER_SECRET not in message
    assert "cache.tests.invalid" not in message


def test_a_tls_broker_that_keeps_the_host_name_check_passes() -> None:
    assert_production_celery(
        always_eager=False,
        broker_url=f"{TLS_BROKER_URL}?ssl_cert_reqs=required&ssl_check_hostname=true",
    )


def test_eager_mode_is_rejected() -> None:
    # A task would run inside the request that enqueued it.
    with pytest.raises(ImproperlyConfigured, match="CELERY_TASK_ALWAYS_EAGER"):
        assert_production_celery(always_eager=True, broker_url=BROKER_URL)


@pytest.mark.parametrize(
    "broker_url",
    ["memory://", f"amqp://queue-user:{BROKER_SECRET}@broker.tests.invalid//", ""],
)
def test_a_broker_that_is_not_valkey_is_rejected(broker_url: str) -> None:
    with pytest.raises(ImproperlyConfigured, match="redis:// or rediss://") as excinfo:
        assert_production_celery(always_eager=False, broker_url=broker_url)

    # The URL embeds a password: the message never echoes it.
    assert BROKER_SECRET not in str(excinfo.value)
    assert "queue-user" not in str(excinfo.value)


def test_both_problems_are_reported_together() -> None:
    with pytest.raises(ImproperlyConfigured) as excinfo:
        assert_production_celery(always_eager=True, broker_url="memory://")

    assert "CELERY_TASK_ALWAYS_EAGER" in str(excinfo.value)
    assert "redis:// or rediss://" in str(excinfo.value)


def test_production_refuses_the_in_memory_broker() -> None:
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        load_production_settings(
            "CELERY_BROKER_URL",
            env={"CELERY_BROKER_URL": "memory://"},
        )

    assert "ImproperlyConfigured" in excinfo.value.stderr
    assert "redis:// or rediss://" in excinfo.value.stderr


def test_production_refuses_another_broker_without_printing_its_url() -> None:
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        load_production_settings(
            "CELERY_BROKER_URL",
            env={
                "CELERY_BROKER_URL": f"amqp://queue-user:{BROKER_SECRET}@broker.tests.invalid//",
            },
        )

    assert "redis:// or rediss://" in excinfo.value.stderr
    assert BROKER_SECRET not in excinfo.value.stderr


def test_production_refuses_eager_mode_if_a_setting_ever_turns_it_on() -> None:
    # No environment variable can enable eager mode. This stands in for a later
    # edit of base.py: production must pass its own value to the guard.
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        run_python(
            "import config.settings.base as base\n"
            "base.CELERY_TASK_ALWAYS_EAGER = True\n"
            "import config.settings.production\n",
        )

    assert "CELERY_TASK_ALWAYS_EAGER must be off" in excinfo.value.stderr


def test_production_refuses_an_explicit_tls_broker_that_does_not_verify() -> None:
    # CELERY_BROKER_URL replaces the URL built from VALKEY_URL, so the guard of
    # VALKEY_URL never sees it.
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        load_production_settings(
            "CELERY_BROKER_URL",
            env={"CELERY_BROKER_URL": TLS_BROKER_URL},
        )

    stderr = excinfo.value.stderr
    assert "The Celery broker uses rediss:// without ssl_cert_reqs=required" in stderr
    assert BROKER_SECRET not in stderr
    assert "cache.tests.invalid" not in stderr


def test_production_accepts_an_explicit_tls_broker_that_verifies() -> None:
    broker_url = f"{TLS_BROKER_URL}?ssl_cert_reqs=required"

    values = load_production_settings(
        "CELERY_BROKER_URL",
        env={"CELERY_BROKER_URL": broker_url},
    )

    assert values["CELERY_BROKER_URL"] == broker_url


def test_production_refuses_an_explicit_tls_broker_that_skips_the_host_name() -> None:
    broker_url = f"{TLS_BROKER_URL}?ssl_cert_reqs=required&ssl_check_hostname=false"

    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        load_production_settings(
            "CELERY_BROKER_URL",
            env={"CELERY_BROKER_URL": broker_url},
        )

    stderr = excinfo.value.stderr
    assert "turns the host name check off with ssl_check_hostname" in stderr
    assert BROKER_SECRET not in stderr
    assert "cache.tests.invalid" not in stderr
