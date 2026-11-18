from __future__ import annotations

import logging
from typing import Any

from django.core import mail
from django.test import override_settings
from django.utils.log import AdminEmailHandler

from core.tests.production_settings import load_production_settings

UNREACHABLE_SMTP: dict[str, Any] = {
    "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
    # Nothing listens on port 1, so every connection is refused at once.
    "OPTIONS": {"host": "127.0.0.1", "port": 1, "use_tls": False, "timeout": 1},
}


def _error_record() -> logging.LogRecord:
    return logging.LogRecord(
        name="django.request",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="Internal Server Error: /api/v1/users/me/",
        args=(),
        exc_info=None,
    )


def test_production_error_emails_use_the_fail_silent_mailer() -> None:
    settings = load_production_settings("LOGGING", "MAILERS")

    assert settings["LOGGING"]["handlers"]["mail_admins"]["using"] == "admin-logging"
    admin_logging = settings["MAILERS"]["admin-logging"]
    default = settings["MAILERS"]["default"]
    assert admin_logging["BACKEND"] == default["BACKEND"]
    assert admin_logging["OPTIONS"] == {**default["OPTIONS"], "fail_silently": True}


@override_settings(
    ADMINS=["admin@example.com"],
    MAILERS={
        "default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"},
        "admin-logging": {
            **UNREACHABLE_SMTP,
            "OPTIONS": {**UNREACHABLE_SMTP["OPTIONS"], "fail_silently": True},
        },
    },
)
def test_admin_email_handler_with_fail_silent_mailer_survives_smtp_outage() -> None:
    AdminEmailHandler(using="admin-logging").emit(_error_record())

    assert mail.outbox == []


def _request_handlers(**env: str) -> list[str]:
    settings = load_production_settings("LOGGING", env=env)
    return settings["LOGGING"]["loggers"]["django.request"]["handlers"]


def test_no_error_emails_without_admins() -> None:
    assert _request_handlers() == ["console"]


def test_admins_from_env_get_error_emails() -> None:
    settings = load_production_settings(
        "ADMINS", env={"DJANGO_ADMINS": '"Ops" <ops@example.org>,dev@example.org'}
    )

    assert settings["ADMINS"] == ['"Ops" <ops@example.org>', "dev@example.org"]
    assert _request_handlers(DJANGO_ADMINS="ops@example.org") == [
        "console",
        "mail_admins",
    ]


def test_sentry_replaces_error_emails() -> None:
    handlers = _request_handlers(
        DJANGO_ADMINS="ops@example.org",
        SENTRY_DSN="https://public@o0.ingest.sentry.invalid/1",
    )

    assert handlers == ["console"]
