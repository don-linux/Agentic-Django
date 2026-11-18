"""The env examples are safe to copy: production refuses them until they are filled."""

from __future__ import annotations

import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from core.tests.production_settings import PRODUCTION_ENV
from core.tests.production_settings import run_python

BASE_DIR = Path(__file__).resolve().parents[2]
PROD_EXAMPLE = BASE_DIR / "docker" / "prod" / ".env.example"
DEV_EXAMPLE = BASE_DIR / ".env.example"


def _assignments(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip("\"'")
    return values


def _production_env(**overrides: str) -> dict[str, str | None]:
    env: dict[str, str | None] = dict(_assignments(PROD_EXAMPLE))
    # Compose supplies this when the example leaves it commented.
    env.setdefault("VALKEY_URL", "redis://valkey:6379")
    env["DJANGO_SETTINGS_MODULE"] = "config.settings.production"
    env.update(overrides)
    return env


def _refusal(env: dict[str, str | None]) -> str:
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        run_python("import config.settings.production\n", env=env)
    assert "ImproperlyConfigured" in excinfo.value.stderr
    return excinfo.value.stderr


def test_a_verbatim_copy_of_the_production_example_is_refused() -> None:
    stderr = _refusal(_production_env())

    for variable in ("DJANGO_SECRET_KEY", "DJANGO_ADMIN_URL", "POSTGRES_PASSWORD"):
        assert variable in stderr


def test_real_secrets_alone_do_not_make_the_example_start() -> None:
    secrets = {
        key: PRODUCTION_ENV[key]
        for key in (
            "DJANGO_SECRET_KEY",
            "DJANGO_ADMIN_URL",
            "POSTGRES_PASSWORD",
            "VALKEY_PASSWORD",
        )
    }

    stderr = _refusal(_production_env(**secrets))

    # The example host refuses until the personalize skill writes the real
    # domain; after that, the blank mailer host still does.
    if _assignments(PROD_EXAMPLE)["DJANGO_ALLOWED_HOSTS"] == "example.com":
        assert "DJANGO_ALLOWED_HOSTS" in stderr
    else:
        assert "DJANGO_MAILER_HOST" in stderr


def test_production_example_points_csrf_and_cors_at_the_frontend() -> None:
    values = _assignments(PROD_EXAMPLE)
    frontend = values["FRONTEND_URL"]

    assert values["DJANGO_CSRF_TRUSTED_ORIGINS"] == frontend
    assert values["CORS_ALLOWED_ORIGINS"] == frontend
    assert urlsplit(frontend).hostname != values["DJANGO_ALLOWED_HOSTS"]


@pytest.mark.parametrize("path", [DEV_EXAMPLE, PROD_EXAMPLE])
def test_both_environments_use_the_django_database_role(path: Path) -> None:
    assert _assignments(path)["POSTGRES_USER"] == "django"
