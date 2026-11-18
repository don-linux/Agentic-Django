"""scripts/local_apps_with_migrations.py feeds CI's migration reversibility check."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.apps import apps
from django.conf import settings

BASE_DIR = Path(__file__).resolve().parents[2]


def _local_apps_with_migration_files() -> list[str]:
    # Independent of the script: any module other than __init__ in migrations/.
    return [
        config.label
        for config in apps.get_app_configs()
        if config.name in settings.LOCAL_APPS
        and any(
            path.name != "__init__.py"
            for path in (Path(config.path) / "migrations").glob("*.py")
        )
    ]


def test_lists_the_local_apps_that_have_migrations() -> None:
    # Run it as CI does: a plain script, from the repo root, with no extra path.
    result = subprocess.run(
        [sys.executable, "scripts/local_apps_with_migrations.py"],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        check=True,
    )

    listed = result.stdout.split()
    assert listed == _local_apps_with_migration_files()
    assert "users" in listed
    assert "core" not in listed  # migrations package, no migration modules


@pytest.mark.parametrize("name", ["project_db", "app", "citest", "testing", "my_test"])
def test_reversible_refuses_a_database_that_is_not_throwaway(name: str) -> None:
    # migrate users zero also unapplies every app that depends on the user
    # model: against the dev database that wipes it. It stops before migrate.
    env = {key: value for key, value in os.environ.items() if key != "DATABASE_URL"}
    env["POSTGRES_DB"] = name
    # A DATABASE_URL-only shell has no other POSTGRES_* variable; the script
    # refuses before it connects, so placeholders are enough.
    for key, value in {
        "POSTGRES_USER": "throwaway",
        "POSTGRES_PASSWORD": "throwaway",
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": "5432",
    }.items():
        env.setdefault(key, value)
    result = subprocess.run(
        ["bash", "scripts/check.sh", "reversible"],  # noqa: S607
        cwd=BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert f"refuses to run against database '{name}'" in result.stderr
    assert "Unapplying" not in result.stdout
