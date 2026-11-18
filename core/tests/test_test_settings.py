"""The test settings stay isolated: nothing in a test run may reach a server."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from django.conf import settings as django_settings

from config import celery_app
from core.tests.production_settings import load_settings

if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest_django.fixtures import Settings

LOCMEM = "django.core.cache.backends.locmem.LocMemCache"
CACHED_DB = "django.contrib.sessions.backends.cached_db"


def test_test_settings_never_use_the_network() -> None:
    backends = {
        alias: config["BACKEND"] for alias, config in django_settings.CACHES.items()
    }

    assert backends == {"default": LOCMEM}


def test_tasks_run_inline_and_never_reach_a_broker() -> None:
    conf = celery_app.conf

    assert conf.task_always_eager is True
    assert conf.task_eager_propagates is True
    # The value Celery really uses: it reads the environment before the setting,
    # so conftest.py removes the variable.
    assert "CELERY_BROKER_URL" not in os.environ
    assert conf.broker_url == django_settings.CELERY_BROKER_URL == "memory://"


def test_sessions_are_cached_and_written_through_to_the_database() -> None:
    # cached_db, never "cache" alone: an eviction or a Valkey restart must not
    # sign everyone out. Tests run the same engine on LocMem.
    assert django_settings.SESSION_ENGINE == CACHED_DB


def test_clearing_refuses_a_cache_that_is_not_in_memory(
    settings: Settings,
    clear_caches: Callable[[], None],
) -> None:
    # On Valkey clear() is FLUSHDB: the fixture has to stop before sending it.
    settings.CACHES = {
        "default": {
            "BACKEND": "core.cache_backend.ValkeyCache",
            "LOCATION": "redis://127.0.0.1:1/15",
        },
    }

    with pytest.raises(TypeError, match="not in memory"):
        clear_caches()


DEV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"
# Values a developer's root .env may hold; dev Compose injects that file.
PROJECT_ENV = {
    "DJANGO_TIME_ZONE": "America/Mexico_City",
    "FRONTEND_URL": "http://localhost:5173",
    "CORS_ALLOWED_ORIGINS": "http://localhost:3000",
    "DJANGO_DEFAULT_FROM_EMAIL": "Acme <noreply@acme.test>",
    "DJANGO_EMAIL_SUBJECT_PREFIX": "[Acme] ",
    "DJANGO_EMAIL_SIGNATURE_NAME": "Acme",
    "DJANGO_ACCOUNT_ALLOW_REGISTRATION": "False",
    "DJANGO_API_MAX_PAGE_NUMBER": "50",
}
# In .env.example but harmless to the tests: test.py sets DEBUG, the key and the
# mailer itself, only local.py reads DJANGO_LOG_SQL, and DJANGO_LOG_FORMAT only
# changes the console formatter.
NOT_PINNED = {
    "DJANGO_DEBUG",
    "DJANGO_SECRET_KEY",
    "DJANGO_MAILER_HOST",
    "DJANGO_MAILER_PORT",
    "DJANGO_LOG_SQL",
    "DJANGO_LOG_FORMAT",
}
CONNECTION = re.compile(r"POSTGRES_\w+|VALKEY_URL")
PINNED = (
    "TIME_ZONE",
    "CELERY_TIMEZONE",
    "FRONTEND_URL",
    "HEADLESS_FRONTEND_URLS",
    "CORS_ALLOWED_ORIGINS",
    "DEFAULT_FROM_EMAIL",
    "SERVER_EMAIL",
    "EMAIL_SUBJECT_PREFIX",
    "ACCOUNT_EMAIL_SUBJECT_PREFIX",
    "EMAIL_SIGNATURE_NAME",
    "MFA_TOTP_ISSUER",
    "ACCOUNT_ALLOW_REGISTRATION",
    "REST_FRAMEWORK_MAX_PAGE_NUMBER",
)


def test_a_project_env_keeps_the_template_defaults_in_the_test_settings() -> None:
    defaults = load_settings(
        "config.settings.base", *PINNED, env=dict.fromkeys(PROJECT_ENV)
    )

    assert load_settings("config.settings.test", *PINNED, env=PROJECT_ENV) == defaults


def test_every_dev_example_variable_is_pinned_or_harmless() -> None:
    names = set(re.findall(r"^#? ?([A-Z][A-Z0-9_]+)=", DEV_EXAMPLE.read_text(), re.M))

    unhandled = {
        name
        for name in names - PROJECT_ENV.keys() - NOT_PINNED
        if not CONNECTION.fullmatch(name)
    }

    assert unhandled == set()
