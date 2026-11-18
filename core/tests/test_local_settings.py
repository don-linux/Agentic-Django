"""Local settings refuse a remote database or Valkey inherited from the shell."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.settings.guards import assert_local_targets
from core.tests.production_settings import load_settings
from core.tests.production_settings import run_python

if TYPE_CHECKING:
    from pathlib import Path

REMOTE_DB = "postgres://operator:prod-db-secret@prod.example.invalid:5432/proddb"
REMOTE_VALKEY = "redis://prod-valkey.example.invalid:6379"
REMOTE_VALKEY_PASSWORD = "super-secret-valkey-password-xxxxxxxx"  # noqa: S105
LOCAL_DB = "postgres://ci:ci@localhost:54318/af1"
LOCAL_VALKEY = "redis://localhost:6379"

# The helper defaults DJANGO_ALLOW_REMOTE_LOCAL=true; tests of the guard turn it off.
GUARD_ON = {"DJANGO_ALLOW_REMOTE_LOCAL": "false"}


def test_loopback_and_compose_hosts_are_accepted() -> None:
    for host in (
        "localhost",
        "127.0.0.1",
        "127.0.0.2",
        "::1",
        "[::1]",
        "",
        "postgres_db",
    ):
        assert_local_targets(
            database_host=host,
            valkey_url="redis://localhost:6379",
            allow_remote=False,
        )
    for url in (
        "redis://localhost:6379",
        "redis://127.0.0.1:6379",
        "redis://valkey:6379",
        "rediss://[::1]:6379",
    ):
        assert_local_targets(
            database_host="localhost",
            valkey_url=url,
            allow_remote=False,
        )


def test_a_remote_database_host_is_refused() -> None:
    with pytest.raises(ImproperlyConfigured, match="POSTGRES_HOST") as excinfo:
        assert_local_targets(
            database_host="prod.example.invalid",
            valkey_url=LOCAL_VALKEY,
            allow_remote=False,
        )
    assert "prod.example.invalid" not in str(excinfo.value)
    assert "DJANGO_ALLOW_REMOTE_LOCAL" in str(excinfo.value)


def test_a_remote_valkey_host_is_refused() -> None:
    with pytest.raises(ImproperlyConfigured, match="VALKEY_URL") as excinfo:
        assert_local_targets(
            database_host="localhost",
            valkey_url=REMOTE_VALKEY,
            allow_remote=False,
        )
    assert "prod-valkey.example.invalid" not in str(excinfo.value)


def test_a_remote_broker_url_is_refused() -> None:
    with pytest.raises(ImproperlyConfigured, match="CELERY_BROKER_URL") as excinfo:
        assert_local_targets(
            database_host="localhost",
            valkey_url=LOCAL_VALKEY,
            celery_broker_url=f"redis://:{REMOTE_VALKEY_PASSWORD}@broker.example.invalid:6379/1",
            allow_remote=False,
        )
    assert REMOTE_VALKEY_PASSWORD not in str(excinfo.value)
    assert "broker.example.invalid" not in str(excinfo.value)


def test_the_escape_hatch_allows_a_remote_host() -> None:
    assert_local_targets(
        database_host="prod.example.invalid",
        valkey_url=REMOTE_VALKEY,
        allow_remote=True,
    )


def test_local_settings_accept_loopback_and_compose_service_names() -> None:
    settings = load_settings(
        "config.settings.local",
        "DEBUG",
        "DATABASES",
        "CACHES",
        env={
            **GUARD_ON,
            "DATABASE_URL": LOCAL_DB,
            "VALKEY_URL": LOCAL_VALKEY,
            "VALKEY_PASSWORD": None,
        },
    )
    assert settings["DEBUG"] is True
    assert settings["DATABASES"]["default"]["HOST"] == "localhost"
    assert settings["CACHES"]["default"]["LOCATION"] == "redis://localhost:6379/0"

    compose = load_settings(
        "config.settings.local",
        "DATABASES",
        "CACHES",
        env={
            **GUARD_ON,
            "DATABASE_URL": None,
            "POSTGRES_DB": "project_db",
            "POSTGRES_USER": "django",
            "POSTGRES_PASSWORD": "dev-password",
            "POSTGRES_HOST": "postgres_db",
            "VALKEY_URL": "redis://valkey:6379",
            "VALKEY_PASSWORD": None,
        },
    )
    assert compose["DATABASES"]["default"]["HOST"] == "postgres_db"
    assert compose["CACHES"]["default"]["LOCATION"] == "redis://valkey:6379/0"


def test_local_settings_refuse_an_inherited_remote_database_url() -> None:
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        load_settings(
            "config.settings.local",
            "DEBUG",
            env={
                **GUARD_ON,
                "DATABASE_URL": REMOTE_DB,
                "VALKEY_URL": LOCAL_VALKEY,
                "VALKEY_PASSWORD": None,
            },
        )

    stderr = excinfo.value.stderr
    assert "ImproperlyConfigured" in stderr
    assert "DJANGO_ALLOW_REMOTE_LOCAL" in stderr
    assert "prod.example.invalid" not in stderr
    assert "prod-db-secret" not in stderr


def test_local_settings_refuse_an_inherited_remote_valkey_url() -> None:
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        load_settings(
            "config.settings.local",
            "DEBUG",
            env={
                **GUARD_ON,
                "DATABASE_URL": LOCAL_DB,
                "VALKEY_URL": REMOTE_VALKEY,
                "VALKEY_PASSWORD": REMOTE_VALKEY_PASSWORD,
            },
        )

    stderr = excinfo.value.stderr
    assert "ImproperlyConfigured" in stderr
    assert "VALKEY_URL" in stderr
    assert "prod-valkey.example.invalid" not in stderr
    assert REMOTE_VALKEY_PASSWORD not in stderr


def test_the_escape_hatch_allows_local_settings_to_import() -> None:
    settings = load_settings(
        "config.settings.local",
        "DEBUG",
        "DATABASES",
        env={
            "DJANGO_ALLOW_REMOTE_LOCAL": "true",
            "DATABASE_URL": REMOTE_DB,
            "VALKEY_URL": REMOTE_VALKEY,
        },
    )
    assert settings["DEBUG"] is True
    assert settings["DATABASES"]["default"]["HOST"] == "prod.example.invalid"


def test_manage_py_default_refuses_a_remote_database_url() -> None:
    """manage.py setdefault to local, then import: the same path as a bare manage.py."""
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        run_python(
            "import os, importlib\n"
            "os.environ.pop('DJANGO_SETTINGS_MODULE', None)\n"
            "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.local')\n"
            "importlib.import_module(os.environ['DJANGO_SETTINGS_MODULE'])\n",
            env={
                **GUARD_ON,
                "DJANGO_SETTINGS_MODULE": None,
                "DATABASE_URL": REMOTE_DB,
                "VALKEY_URL": LOCAL_VALKEY,
                "VALKEY_PASSWORD": None,
            },
        )

    assert "ImproperlyConfigured" in excinfo.value.stderr
    assert "prod-db-secret" not in excinfo.value.stderr


def test_a_remote_database_from_the_env_file_is_refused(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgres://ops:secret@db.prod.internal:5432/app\n"
    )

    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        load_settings(
            "config.settings.local",
            "DATABASES",
            env={"DJANGO_ENV_FILE": str(env_file), "DJANGO_ALLOW_REMOTE_LOCAL": None},
        )

    assert "DATABASE_URL" in excinfo.value.stderr
    assert "db.prod.internal" not in excinfo.value.stderr
