"""The root .env is read by the local settings only (config/settings/_env_file.py)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.tests.production_settings import load_settings

if TYPE_CHECKING:
    from pathlib import Path


def _env_file(tmp_path: Path, *lines: str) -> str:
    path = tmp_path / ".env"
    path.write_text("\n".join(lines) + "\n")
    return str(path)


def test_local_settings_read_the_env_file(tmp_path: Path) -> None:
    env_file = _env_file(tmp_path, "DJANGO_TIME_ZONE=America/Mexico_City")

    values = load_settings(
        "config.settings.local",
        "TIME_ZONE",
        "ENV_FILE",
        env={"DJANGO_ENV_FILE": env_file},
    )

    assert values == {"TIME_ZONE": "America/Mexico_City", "ENV_FILE": env_file}


def test_the_environment_wins_over_the_env_file(tmp_path: Path) -> None:
    env_file = _env_file(tmp_path, "DJANGO_TIME_ZONE=America/Mexico_City")

    values = load_settings(
        "config.settings.local",
        "TIME_ZONE",
        env={"DJANGO_ENV_FILE": env_file, "DJANGO_TIME_ZONE": "Europe/Madrid"},
    )

    assert values["TIME_ZONE"] == "Europe/Madrid"


def test_a_value_is_read_as_written(tmp_path: Path) -> None:
    # No ${...} expansion and no $NAME lookup: a secret may contain either.
    env_file = _env_file(
        tmp_path,
        "DJANGO_EMAIL_SIGNATURE_NAME=${DJANGO_TIME_ZONE}",
        'DJANGO_EMAIL_SUBJECT_PREFIX="$DJANGO_TIME_ZONE "',
        "DJANGO_TIME_ZONE=America/Mexico_City",
    )

    values = load_settings(
        "config.settings.local",
        "EMAIL_SIGNATURE_NAME",
        "EMAIL_SUBJECT_PREFIX",
        env={"DJANGO_ENV_FILE": env_file},
    )

    assert values == {
        "EMAIL_SIGNATURE_NAME": "${DJANGO_TIME_ZONE}",
        "EMAIL_SUBJECT_PREFIX": "$DJANGO_TIME_ZONE ",
    }


def test_an_empty_path_turns_the_read_off() -> None:
    values = load_settings("config.settings.local", "TIME_ZONE", "ENV_FILE")

    assert values == {"TIME_ZONE": "UTC", "ENV_FILE": ""}


def test_a_missing_file_is_not_an_error(tmp_path: Path) -> None:
    missing = str(tmp_path / "absent.env")

    values = load_settings(
        "config.settings.local",
        "TIME_ZONE",
        env={"DJANGO_ENV_FILE": missing},
    )

    assert values["TIME_ZONE"] == "UTC"


@pytest.mark.parametrize(
    "module", ["config.settings.production", "config.settings.test"]
)
def test_production_and_test_settings_never_read_an_env_file(
    tmp_path: Path, module: str
) -> None:
    env_file = _env_file(
        tmp_path,
        "DJANGO_TIME_ZONE=Junk/Zone",
        "DJANGO_DEBUG=True",
        "DJANGO_SECRET_KEY=change-me",
    )

    values = load_settings(
        module, "TIME_ZONE", "DEBUG", env={"DJANGO_ENV_FILE": env_file}
    )

    assert values == {"TIME_ZONE": "UTC", "DEBUG": False}
