"""Values that pass the production guards, shared by the settings tests."""

from __future__ import annotations

from typing import Any

from config.settings.guards import SMTP_BACKEND

STRONG_KEY = "tests-only-Kq8vN2xR7mPz4wLs9TjB6yHc3FdG5aUe1oIk0nMbVlXrQ"
FAKE_DSN = "https://public@o0.ingest.sentry.invalid/1"

VALID_SECRETS: dict[str, Any] = {
    "secret_key": STRONG_KEY,
    "secret_key_fallbacks": [],
    "admin_url": "staff-7f3k/",
    "db_password": "a-real-db-password",
}


def _smtp(**options: Any) -> dict[str, Any]:
    base = {"host": "smtp.postmarkapp.com", "use_tls": True, "use_ssl": False}
    return {"default": {"BACKEND": SMTP_BACKEND, "OPTIONS": {**base, **options}}}


VALID_EMAIL: dict[str, Any] = {
    "mailers": _smtp(),
    "default_from_email": "Acme <noreply@acme.invalid>",
    "frontend_url": "https://app.acme.invalid",
}
