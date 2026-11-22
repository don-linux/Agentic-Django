"""The env examples are safe to copy: production refuses them until they are filled."""

from __future__ import annotations

import re
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


# One assignment per line, commented or not: a commented example is meant to be
# uncommented as is.
_ASSIGNMENT = re.compile(r"^(?:# ?)?([A-Z][A-Z0-9_]*)=(.*)$")


def _unquoted_values_with_spaces(text: str) -> list[str]:
    """Lines that uv run --env-file (dotenvy) refuses: whitespace outside quotes.

    uv stops reading the file at such a line, with only a warning: that
    variable and every one after it stay unset.
    """
    refused = []
    for line in text.splitlines():
        match = _ASSIGNMENT.match(line)
        if match is None:
            continue
        value = match.group(2)
        quoted = len(value) > 1 and value[0] == value[-1] and value[0] in "\"'"
        if not quoted and re.search(r"\s", value):
            refused.append(line)
    return refused


@pytest.mark.parametrize(
    "path",
    [DEV_EXAMPLE, PROD_EXAMPLE, BASE_DIR / "docker" / "dev" / ".env.example"],
    ids=["dev", "prod", "compose-dev"],
)
def test_every_value_with_spaces_is_quoted(path: Path) -> None:
    assert _unquoted_values_with_spaces(path.read_text()) == []


def test_only_unquoted_values_with_spaces_are_refused() -> None:
    # Each line takes another branch: a plain and a commented assignment with
    # a space, a double- and a single-quoted value with one. The real files
    # above cover values without spaces.
    refused = [
        "DJANGO_EMAIL_SIGNATURE_NAME=Acme Backend",
        "# DJANGO_EMAIL_SUBJECT_PREFIX=[Acme Backend] ",
    ]
    accepted = [
        'DJANGO_EMAIL_SIGNATURE_NAME="Acme Backend"',
        "DJANGO_ADMINS='\"Ops\" <ops@example.com>'",
    ]
    assert _unquoted_values_with_spaces("\n".join(refused + accepted)) == refused


PERSONALIZE_SKILL = (
    BASE_DIR / ".agents" / "skills" / "personalize-django-template" / "SKILL.md"
)
# A `KEY=value` the skill tells the agent to write, with the project name in it.
_SKILL_ASSIGNMENT = re.compile(r"`(#? ?[A-Z][A-Z0-9_]*=[^`]*\{Nombre\}[^`]*)`")


def test_the_personalize_skill_quotes_every_value_with_the_project_name() -> None:
    # What the skill makes the agent write for a two-word name.
    written = [
        line.replace("{Nombre}", "Acme Backend").replace("{dominio}", "acme.dev")
        for line in _SKILL_ASSIGNMENT.findall(PERSONALIZE_SKILL.read_text())
    ]
    assert written
    assert _unquoted_values_with_spaces("\n".join(written)) == []
