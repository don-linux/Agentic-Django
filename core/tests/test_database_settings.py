from __future__ import annotations

import pytest
from django.conf import settings

from config.settings.base import build_postgres_options
from core.tests.production_settings import PRODUCTION_ENV
from core.tests.production_settings import load_production_settings
from core.tests.production_settings import load_settings

# Production keeps connections for a minute; a URL's port is an int, not a string.
CONN_MAX_AGE_SECONDS = 60
URL_PORT = 5433
VARS_PASSWORD = "vars-password-ok"  # noqa: S105
URL_PASSWORD = "url-pass"  # noqa: S105


def test_connect_timeout_is_set() -> None:
    assert settings.DATABASES["default"]["OPTIONS"]["connect_timeout"] == 5  # noqa: PLR2004


@pytest.mark.parametrize(
    ("statement_ms", "lock_ms", "expected"),
    [
        (0, 0, ""),
        (30000, 0, "-c statement_timeout=30000"),
        (0, 10000, "-c lock_timeout=10000"),
        (30000, 10000, "-c statement_timeout=30000 -c lock_timeout=10000"),
    ],
)
def test_postgres_options_include_only_positive_timeouts(
    statement_ms: int,
    lock_ms: int,
    expected: str,
) -> None:
    options = build_postgres_options(
        statement_timeout_ms=statement_ms,
        lock_timeout_ms=lock_ms,
    )

    assert options == expected


def test_production_bounds_connections_and_queries() -> None:
    database = load_production_settings("DATABASES")["DATABASES"]["default"]

    assert database["CONN_HEALTH_CHECKS"] is True
    assert database["CONN_MAX_AGE"] == CONN_MAX_AGE_SECONDS
    assert database["NAME"] == "tests"
    assert database["USER"] == "tests"
    assert database["PASSWORD"] == PRODUCTION_ENV["POSTGRES_PASSWORD"]
    assert database["OPTIONS"]["connect_timeout"] == 5  # noqa: PLR2004
    assert database["OPTIONS"]["options"] == "-c statement_timeout=30000"


def test_production_migrate_settings_drop_the_statement_timeout() -> None:
    database = load_production_settings(
        "DATABASES",
        env={"POSTGRES_STATEMENT_TIMEOUT_MS": "0", "POSTGRES_LOCK_TIMEOUT_MS": "10000"},
    )["DATABASES"]["default"]

    assert database["OPTIONS"]["options"] == "-c lock_timeout=10000"


def test_production_without_timeouts_sends_no_options() -> None:
    database = load_production_settings(
        "DATABASES",
        env={"POSTGRES_STATEMENT_TIMEOUT_MS": "0"},
    )["DATABASES"]["default"]

    assert "options" not in database["OPTIONS"]


def test_databases_come_from_postgres_vars_when_url_is_unset() -> None:
    database = load_settings(
        "config.settings.local",
        "DATABASES",
        env={
            "DATABASE_URL": None,
            "POSTGRES_DB": "from-vars",
            "POSTGRES_USER": "vars-user",
            "POSTGRES_PASSWORD": VARS_PASSWORD,
            "POSTGRES_HOST": "vars-host",
            "POSTGRES_PORT": "6543",
        },
    )["DATABASES"]["default"]

    assert database["ENGINE"] == "django.db.backends.postgresql"
    assert database["NAME"] == "from-vars"
    assert database["USER"] == "vars-user"
    assert database["PASSWORD"] == VARS_PASSWORD
    assert database["HOST"] == "vars-host"
    assert database["PORT"] == "6543"


def test_database_url_wins_over_postgres_vars() -> None:
    database = load_settings(
        "config.settings.local",
        "DATABASES",
        env={
            "DATABASE_URL": f"postgres://url-user:{URL_PASSWORD}@url-host:5433/url-db",
            "POSTGRES_DB": "from-vars",
            "POSTGRES_USER": "vars-user",
            "POSTGRES_PASSWORD": VARS_PASSWORD,
            "POSTGRES_HOST": "vars-host",
            "POSTGRES_PORT": "6543",
        },
    )["DATABASES"]["default"]

    assert database["NAME"] == "url-db"
    assert database["USER"] == "url-user"
    assert database["PASSWORD"] == URL_PASSWORD
    assert database["HOST"] == "url-host"
    assert database["PORT"] == URL_PORT
