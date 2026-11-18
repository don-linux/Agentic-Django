from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.settings.guards import assert_production_hosts
from config.settings.guards import assert_production_secrets
from core.tests.guard_values import STRONG_KEY
from core.tests.guard_values import VALID_SECRETS as VALID
from core.tests.production_settings import BASE_DIR
from core.tests.production_settings import load_production_settings
from core.tests.production_settings import load_settings

# Production's HSTS max-age until the operator ramps it (docs/security.md#hsts).
DEFAULT_HSTS_SECONDS = 60

VALID_HOSTS: dict[str, Any] = {
    "allowed_hosts": ["api.tests.invalid"],
    "csrf_trusted_origins": ["https://app.tests.invalid"],
}


def _assert_rejected(match: str, **overrides: Any) -> None:
    with pytest.raises(ImproperlyConfigured, match=match) as excinfo:
        assert_production_secrets(**{**VALID, **overrides})
    for value in overrides.values():
        if isinstance(value, str) and value:
            # Messages name the variable but never echo the secret.
            assert value not in str(excinfo.value)


def _assert_hosts_rejected(match: str, **overrides: Any) -> None:
    with pytest.raises(ImproperlyConfigured, match=match) as excinfo:
        assert_production_hosts(**{**VALID_HOSTS, **overrides})
    for value in overrides.values():
        if isinstance(value, str) and value:
            assert value not in str(excinfo.value)


def test_valid_settings_pass() -> None:
    assert_production_secrets(**VALID)
    assert_production_hosts(**VALID_HOSTS)


def test_valid_fallback_passes() -> None:
    assert_production_secrets(
        **{**VALID, "secret_key_fallbacks": [STRONG_KEY[::-1]]},
    )


def test_a_weak_secret_key_is_rejected_even_with_a_valid_fallback() -> None:
    # A later good fallback must not drop problems from a weak primary key
    # (the `problems +=` / `problems =` mutant).
    _assert_rejected(
        "DJANGO_SECRET_KEY",
        secret_key="change-me",  # noqa: S106
        secret_key_fallbacks=[STRONG_KEY[::-1]],
    )


@pytest.mark.parametrize(
    "secret_key",
    [
        "change-me",
        "CHANGE-ME-" + "x" * 60,
        "changeme" + "x" * 60,
        "change_me-" + "x" * 60,
        "change-me-" + "x" * 60,
        "django-insecure-" + "Kq8vN2xR7mPz4wLs9TjB6yHc3FdG5aUe1oIk0nMbVl",
        "short-but-random-Kq8vN2",
        "a" * 60,
        "abab" * 20,
    ],
)
def test_weak_secret_key_is_rejected(secret_key: str) -> None:
    _assert_rejected("DJANGO_SECRET_KEY", secret_key=secret_key)


def test_weak_fallback_is_rejected() -> None:
    _assert_rejected(
        r"DJANGO_SECRET_KEY_FALLBACKS \(entry 2\)",
        secret_key_fallbacks=[STRONG_KEY[::-1], "change-me"],
    )


@pytest.mark.parametrize(
    "admin_url",
    ["change-me/", "Change-Me/", "admin/", "staff-7f3k"],
)
def test_example_or_default_admin_url_is_rejected(admin_url: str) -> None:
    _assert_rejected("DJANGO_ADMIN_URL", admin_url=admin_url)


@pytest.mark.parametrize(
    "db_password",
    ["change-me", "CHANGE-ME", "changeme", "change_me", "change me"],
)
def test_example_db_password_is_rejected(db_password: str) -> None:
    _assert_rejected("POSTGRES_PASSWORD", db_password=db_password)


def test_empty_db_password_is_rejected() -> None:
    _assert_rejected("POSTGRES_PASSWORD", db_password="")


def test_unset_db_password_is_allowed() -> None:
    # No password at all: peer auth or a DATABASE_URL without one.
    assert_production_secrets(**{**VALID, "db_password": None})


def test_wildcard_allowed_hosts_is_rejected() -> None:
    _assert_hosts_rejected("DJANGO_ALLOWED_HOSTS", allowed_hosts=["*"])


@pytest.mark.parametrize(
    "allowed_hosts",
    [
        [],
        ["example.com"],
        ["EXAMPLE.COM"],
        ["app.example.com"],
        [".example.com"],
        ["example.org"],
        ["api.example.net"],
        ["localhost"],
        ["127.0.0.1"],
        ["foo.localhost"],
    ],
)
def test_example_or_empty_allowed_hosts_are_rejected(
    allowed_hosts: list[str],
) -> None:
    _assert_hosts_rejected("DJANGO_ALLOWED_HOSTS", allowed_hosts=allowed_hosts)


@pytest.mark.parametrize(
    "allowed_hosts",
    [
        ["api.tests.invalid"],
        ["api.ci.invalid"],
        ["notexample.com"],
        ["example.com.mx"],
    ],
)
def test_real_or_reserved_test_hosts_pass(allowed_hosts: list[str]) -> None:
    assert_production_hosts(**{**VALID_HOSTS, "allowed_hosts": allowed_hosts})


@pytest.mark.parametrize(
    "csrf_trusted_origins",
    [
        ["https://example.com"],
        ["https://app.example.com"],
        ["https://example.org"],
        ["http://localhost:3000"],
    ],
)
def test_example_csrf_origins_are_rejected(
    csrf_trusted_origins: list[str],
) -> None:
    _assert_hosts_rejected(
        "DJANGO_CSRF_TRUSTED_ORIGINS",
        csrf_trusted_origins=csrf_trusted_origins,
    )


def test_empty_csrf_origins_pass() -> None:
    # Token-only or same-origin clients may not need a cross-origin CSRF list.
    assert_production_hosts(**{**VALID_HOSTS, "csrf_trusted_origins": []})


def test_all_problems_are_reported_together() -> None:
    with pytest.raises(ImproperlyConfigured) as excinfo:
        assert_production_secrets(
            **{**VALID, "secret_key": "change-me", "admin_url": "admin/"},
        )
    message = str(excinfo.value)
    assert "DJANGO_SECRET_KEY" in message
    assert "DJANGO_ADMIN_URL" in message


def test_production_module_calls_the_guard() -> None:
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        load_production_settings("SECRET_KEY", env={"DJANGO_SECRET_KEY": "change-me"})

    assert "ImproperlyConfigured" in excinfo.value.stderr
    assert "DJANGO_SECRET_KEY" in excinfo.value.stderr


def test_production_reads_secret_key_fallbacks() -> None:
    fallback = STRONG_KEY[::-1]
    settings = load_production_settings(
        "SECRET_KEY_FALLBACKS",
        env={"DJANGO_SECRET_KEY_FALLBACKS": fallback},
    )

    assert settings["SECRET_KEY_FALLBACKS"] == [fallback]


@pytest.mark.parametrize("module", ["config.settings.local", "config.settings.test"])
def test_development_keys_are_marked_insecure(module: str) -> None:
    # With DJANGO_SECRET_KEY unset, SECRET_KEY is the module's default.
    settings = load_settings(module, "SECRET_KEY", env={"DJANGO_SECRET_KEY": None})

    assert settings["SECRET_KEY"].startswith("django-insecure-")


def test_production_rejects_shipped_example_hosts() -> None:
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        load_production_settings(
            "ALLOWED_HOSTS",
            env={"DJANGO_ALLOWED_HOSTS": "example.com"},
        )
    assert "DJANGO_ALLOWED_HOSTS" in excinfo.value.stderr


def test_production_rejects_unset_allowed_hosts() -> None:
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        load_production_settings(
            "ALLOWED_HOSTS",
            env={"DJANGO_ALLOWED_HOSTS": None},
        )
    # django-environ raises if the required variable is missing; the message
    # names the variable either way.
    assert "DJANGO_ALLOWED_HOSTS" in excinfo.value.stderr


def test_production_rejects_shipped_csrf_origin() -> None:
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        load_production_settings(
            "CSRF_TRUSTED_ORIGINS",
            env={"DJANGO_CSRF_TRUSTED_ORIGINS": "https://example.com"},
        )
    assert "DJANGO_CSRF_TRUSTED_ORIGINS" in excinfo.value.stderr


def test_ci_like_env_is_accepted() -> None:
    settings = load_production_settings(
        "ALLOWED_HOSTS",
        "DEFAULT_FROM_EMAIL",
        "FRONTEND_URL",
        env={
            "DJANGO_ALLOWED_HOSTS": "api.ci.invalid",
            "DJANGO_MAILER_HOST": "smtp.ci.invalid",
            "DJANGO_DEFAULT_FROM_EMAIL": "CI <noreply@ci.invalid>",
            "FRONTEND_URL": "https://app.ci.invalid",
            "POSTGRES_PASSWORD": "ci",
        },
    )
    assert settings["ALLOWED_HOSTS"] == ["api.ci.invalid"]


def test_the_image_build_env_passes_the_production_guards() -> None:
    # The image build gives collectstatic only the Dockerfile's own values. Run it
    # with exactly those, so a variable a guard starts to require fails here too,
    # not only in a clean `docker build`. Never PRODUCTION_ENV or this shell's env.
    collectstatic = "python manage.py collectstatic --noinput"
    dockerfile = (BASE_DIR / "docker/prod/Dockerfile").read_text()
    [run] = [
        line.removeprefix("RUN ")
        for line in dockerfile.replace("\\\n", " ").splitlines()
        if collectstatic in line
    ]
    assignments, _, _ = run.partition(collectstatic)
    assert "DJANGO_SETTINGS_MODULE=config.settings.production" in dockerfile
    env = {
        "PATH": os.pathsep.join([str(Path(sys.executable).parent), os.environ["PATH"]]),
        "DJANGO_SETTINGS_MODULE": "config.settings.production",
    }

    result = subprocess.run(  # noqa: S603
        ["/bin/sh", "-c", f"{assignments}{collectstatic} --dry-run --verbosity 0"],
        cwd=BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_production_rejects_uppercase_placeholder_secret() -> None:
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        load_production_settings(
            "SECRET_KEY",
            env={"DJANGO_SECRET_KEY": "CHANGE-ME-" * 5},
        )
    assert "DJANGO_SECRET_KEY" in excinfo.value.stderr


def test_database_url_placeholder_password_is_rejected() -> None:
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        load_production_settings(
            "DATABASES",
            env={
                "DATABASE_URL": "postgres://django:CHANGE-ME@db.tests.invalid:5432/project_db",
            },
        )
    assert "POSTGRES_PASSWORD" in excinfo.value.stderr


def test_database_url_without_a_password_is_allowed() -> None:
    # Peer or trust auth: the URL carries no password at all.
    settings = load_production_settings(
        "DATABASES",
        env={
            "DATABASE_URL": "postgres://django@db.tests.invalid:5432/project_db",
            "POSTGRES_PASSWORD": None,
        },
    )
    assert settings["DATABASES"]["default"]["PASSWORD"] == ""


def test_empty_postgres_password_without_database_url_is_refused() -> None:
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        load_production_settings(
            "DATABASES",
            env={"DATABASE_URL": None, "POSTGRES_PASSWORD": ""},
        )
    assert "POSTGRES_PASSWORD must not be empty" in excinfo.value.stderr


def test_database_url_password_is_used_when_postgres_password_is_empty() -> None:
    settings = load_production_settings(
        "DATABASES",
        env={
            "DATABASE_URL": (
                "postgres://django:tests-only-db-password@db.tests.invalid:5432/project_db"
            ),
            "POSTGRES_PASSWORD": "",
        },
    )
    assert settings["DATABASES"]["default"]["PASSWORD"] == "tests-only-db-password"  # noqa: S105


def test_production_https_and_cookie_defaults() -> None:
    settings = load_production_settings(
        "DEBUG",
        "SECURE_PROXY_SSL_HEADER",
        "SECURE_SSL_REDIRECT",
        "SESSION_COOKIE_SECURE",
        "SESSION_COOKIE_NAME",
        "CSRF_COOKIE_SECURE",
        "CSRF_COOKIE_NAME",
        "SECURE_HSTS_SECONDS",
        "SECURE_HSTS_INCLUDE_SUBDOMAINS",
        "SECURE_HSTS_PRELOAD",
        "SECURE_CONTENT_TYPE_NOSNIFF",
        "SILENCED_SYSTEM_CHECKS",
    )

    assert settings["DEBUG"] is False
    assert settings["SECURE_PROXY_SSL_HEADER"] == ["HTTP_X_FORWARDED_PROTO", "https"]
    assert settings["SECURE_SSL_REDIRECT"] is True
    assert settings["SESSION_COOKIE_SECURE"] is True
    assert settings["SESSION_COOKIE_NAME"] == "__Secure-sessionid"
    assert settings["CSRF_COOKIE_SECURE"] is True
    assert settings["CSRF_COOKIE_NAME"] == "__Secure-csrftoken"
    assert settings["SECURE_HSTS_SECONDS"] == DEFAULT_HSTS_SECONDS
    assert settings["SECURE_HSTS_INCLUDE_SUBDOMAINS"] is False
    assert settings["SECURE_HSTS_PRELOAD"] is False
    assert settings["SECURE_CONTENT_TYPE_NOSNIFF"] is True
    assert settings["SILENCED_SYSTEM_CHECKS"] == ["security.W005", "security.W021"]


def test_session_and_csrf_cookies_are_same_site_lax() -> None:
    values = load_production_settings("SESSION_COOKIE_SAMESITE", "CSRF_COOKIE_SAMESITE")

    assert values == {"SESSION_COOKIE_SAMESITE": "Lax", "CSRF_COOKIE_SAMESITE": "Lax"}
