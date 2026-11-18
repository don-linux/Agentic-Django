# ruff: noqa: INP001
"""
Print the labels of local apps that have migrations, one per line.

Read from disk, no database. See docs/ci.md#reversible-migrations.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import django
from django.apps import apps
from django.conf import settings
from django.db.migrations.loader import MigrationLoader

REPO_ROOT = Path(__file__).resolve().parents[1]


def local_apps_with_migrations() -> list[str]:
    loader = MigrationLoader(None, ignore_no_migrations=True)
    with_migrations = {app_label for app_label, _ in loader.disk_migrations}
    return [
        config.label
        for config in apps.get_app_configs()
        if config.name in settings.LOCAL_APPS and config.label in with_migrations
    ]


def main() -> None:
    # Run as `python scripts/...`: the repo root (config/) is not on sys.path.
    sys.path.insert(0, str(REPO_ROOT))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")
    django.setup()
    sys.stdout.write("".join(f"{label}\n" for label in local_apps_with_migrations()))


if __name__ == "__main__":
    main()
