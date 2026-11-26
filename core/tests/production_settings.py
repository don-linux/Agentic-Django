"""Read `config.settings.production` from tests without touching the active settings."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from collections.abc import Mapping

BASE_DIR = Path(__file__).resolve().parents[2]

# Throwaway values the production module requires. They must never be example values.
PRODUCTION_ENV = {
    "DJANGO_SECRET_KEY": "tests-only-production-secret-" + "k" * 40,
    "DJANGO_ADMIN_URL": "tests-admin/",
    "DJANGO_ALLOWED_HOSTS": "api.tests.invalid",
    "DJANGO_CSRF_TRUSTED_ORIGINS": "https://app.tests.invalid",
    "DJANGO_MAILER_HOST": "smtp.tests.invalid",
    "DJANGO_DEFAULT_FROM_EMAIL": "Tests <noreply@tests.invalid>",
    "FRONTEND_URL": "https://app.tests.invalid",
    "POSTGRES_DB": "tests",
    "POSTGRES_USER": "tests",
    "POSTGRES_PASSWORD": "tests-only-db-password",
    "VALKEY_URL": "redis://valkey.tests.invalid:6379",
    "VALKEY_PASSWORD": "tests-only-valkey-password-" + "k" * 20,
}

# Variables of the developer's shell or of the dev container (VALKEY_URL comes from
# compose) must never leak into the settings a subprocess test loads.
_CLEARED_PREFIXES = (
    "DJANGO_",
    "POSTGRES_",
    "ALLAUTH_",
    "SENTRY_",
    "VALKEY_",
    "CELERY_",
    "CORS_",
)

_LOADER = (
    "import importlib, json, sys\n"
    "settings = importlib.import_module(sys.argv[1])\n"
    "values = {name: getattr(settings, name) for name in sys.argv[2:]}\n"
    "print(json.dumps(values, default=str))\n"
)


def load_production_settings(
    *names: str,
    env: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    return load_settings("config.settings.production", *names, env=env)


def load_settings(
    module: str,
    *names: str,
    env: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    """
    Import a settings module in a subprocess and return `names`.

    The subprocess gets a cleared environment plus PRODUCTION_ENV and `env`; a None
    value in `env` unsets that variable.
    See docs/development.md#production-settings-loader.
    """
    result = run_python(_LOADER, module, *names, env=env)
    return json.loads(result.stdout)


def run_python(
    code: str,
    *args: str,
    env: Mapping[str, str | None] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run `code` in a fresh interpreter with the environment load_settings uses."""
    child_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(_CLEARED_PREFIXES)
        and key not in {"FRONTEND_URL", "DATABASE_URL"}
    }
    child_env.update(PRODUCTION_ENV)
    # Local settings read the root .env unless this is empty: the developer's file
    # must never reach a subprocess test.
    child_env["DJANGO_ENV_FILE"] = ""
    # local.py refuses remote hosts unless this is set, and PRODUCTION_ENV names
    # reserved .invalid hosts. The tests of that guard unset it.
    child_env.setdefault("DJANGO_ALLOW_REMOTE_LOCAL", "true")
    for key, value in (env or {}).items():
        if value is None:
            child_env.pop(key, None)
        else:
            child_env[key] = value
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", code, *args],
        cwd=BASE_DIR,
        env=child_env,
        capture_output=True,
        text=True,
        check=True,
    )
