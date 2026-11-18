from __future__ import annotations

from typing import TYPE_CHECKING

from django.views.debug import get_default_exception_reporter_filter

from core.debug import SafeExceptionReporterFilter

if TYPE_CHECKING:
    from pytest_django.fixtures import Settings

VALKEY_SECRET = "tests-only-valkey-secret"  # noqa: S105
BROKER_URL = f"redis://:{VALKEY_SECRET}@valkey:6379/1"
LOCATION = "redis://valkey:6379/0"
SENTRY_KEY = "testsonlysentrypublickey"
SENTRY_DSN = f"https://{SENTRY_KEY}@o0.ingest.sentry.invalid/1"


def test_the_project_filter_is_the_default() -> None:
    default = get_default_exception_reporter_filter()

    assert isinstance(default, SafeExceptionReporterFilter)


def test_the_broker_url_is_hidden() -> None:
    cleansed = SafeExceptionReporterFilter().cleanse_setting(
        "CELERY_BROKER_URL",
        BROKER_URL,
    )

    assert VALKEY_SECRET not in cleansed


def test_a_dsn_is_hidden() -> None:
    # Django's own pattern lets SENTRY_DSN through, key included.
    cleansed = SafeExceptionReporterFilter().cleanse_setting("SENTRY_DSN", SENTRY_DSN)

    assert SENTRY_KEY not in cleansed


def test_cache_options_hide_the_password_and_keep_the_location() -> None:
    cleansed = SafeExceptionReporterFilter().cleanse_setting(
        "CACHES",
        {"default": {"LOCATION": LOCATION, "OPTIONS": {"password": VALKEY_SECRET}}},
    )

    assert cleansed["default"]["LOCATION"] == LOCATION


def test_safe_settings_never_contain_the_valkey_password(settings: Settings) -> None:
    settings.VALKEY_PASSWORD = VALKEY_SECRET
    settings.CELERY_BROKER_URL = BROKER_URL
    settings.SENTRY_DSN = SENTRY_DSN

    safe_settings = SafeExceptionReporterFilter().get_safe_settings()

    assert VALKEY_SECRET not in repr(safe_settings)
    assert SENTRY_KEY not in repr(safe_settings)
