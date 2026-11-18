"""Background work goes through Celery: apps/ and core/ never import django.tasks."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[2]
SCANNED = ("apps", "core")
BANNED = "django.tasks"
RULE = "AGENTS.md, Background tasks (Celery)"


def _is_banned(name: str) -> bool:
    return name == BANNED or name.startswith(f"{BANNED}.")


def _django_tasks_imports(source: str) -> list[int]:
    """Return the line of every statement in `source` that imports django.tasks."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            module = node.module or ""
            # `from django import tasks` names the module in the alias.
            names = [module, *(f"{module}.{alias.name}" for alias in node.names)]
        else:
            continue
        if any(_is_banned(name) for name in names):
            lines.append(node.lineno)
    return sorted(lines)


def _scan(root: Path) -> tuple[int, list[str]]:
    """Read apps/ and core/ under `root`: files read, and `path:line` per import."""
    files = sorted(path for folder in SCANNED for path in (root / folder).rglob("*.py"))
    found = [
        f"{path.relative_to(root).as_posix()}:{line}"
        for path in files
        for line in _django_tasks_imports(path.read_text(encoding="utf-8"))
    ]
    return len(files), found


def test_nothing_in_apps_or_core_imports_django_tasks() -> None:
    scanned, found = _scan(BASE_DIR)

    assert scanned, "no Python file was scanned"
    assert found == [], (
        "These lines import django.tasks, whose backend runs nothing here. Use "
        f"Celery's @shared_task and core.tasks.enqueue_on_commit ({RULE}): {found}"
    )


@pytest.mark.parametrize(
    "source",
    [
        "from django.tasks import task\n",
        "from django.tasks import task as background\n",
        "import django.tasks\n",
        "import django.tasks as django_tasks\n",
        "import os, django.tasks.base\n",
        "from django import tasks\n",
        "from django import forms, tasks\n",
        "from django.tasks.backends.immediate import ImmediateBackend\n",
        "def enqueue():\n    from django.tasks import task\n    return task\n",
        "try:\n    import django.tasks\nexcept ImportError:\n    pass\n",
    ],
)
def test_the_checker_flags_every_way_of_importing_django_tasks(source: str) -> None:
    assert _django_tasks_imports(source) != []


@pytest.mark.parametrize(
    "source",
    [
        "from celery import shared_task\n",
        "from core.tasks import enqueue_on_commit\n",
        "from apps.users import tasks\n",
        "from . import tasks\n",
        "from .tasks import ping\n",
        "import django\n",
        "from django import forms\n",
        # A module whose name only starts the same way.
        "import django.tasks_registry\n",
        "from django.tasksets import task\n",
        # The setting names the backend as a string, and comments are not code.
        'BACKEND = "django.tasks.backends.dummy.DummyBackend"\n',
        "# from django.tasks import task\n",
        '"""Do not write `from django.tasks import task` here."""\n',
    ],
)
def test_the_checker_accepts_celery_and_local_task_modules(source: str) -> None:
    assert _django_tasks_imports(source) == []


def test_the_checker_reports_every_import_with_its_line() -> None:
    source = (
        "import os\n"
        "\n"
        "from django.tasks import task\n"
        "\n"
        "\n"
        "def later():\n"
        "    import django.tasks\n"
    )

    assert _django_tasks_imports(source) == [3, 7]


def test_the_scan_covers_apps_and_core_and_names_the_file(tmp_path: Path) -> None:
    (tmp_path / "apps" / "shop").mkdir(parents=True)
    (tmp_path / "core" / "tests").mkdir(parents=True)
    (tmp_path / "config").mkdir()
    (tmp_path / "apps" / "shop" / "tasks.py").write_text(
        "import os\n\nfrom django.tasks import task\n",
    )
    (tmp_path / "core" / "tests" / "test_reports.py").write_text(
        "from django import tasks\n",
    )
    (tmp_path / "core" / "tasks.py").write_text("from celery import shared_task\n")
    # Settings name the backend that runs nothing; config/ is not scanned.
    (tmp_path / "config" / "settings.py").write_text("import django.tasks\n")

    assert _scan(tmp_path) == (
        3,
        ["apps/shop/tasks.py:3", "core/tests/test_reports.py:1"],
    )
