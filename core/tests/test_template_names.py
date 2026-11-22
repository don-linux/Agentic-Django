"""The template carries no project name outside README.md's title.

Code, docs and config use generic placeholders ("project", "Project API"), so a
new project only renames README.md (and pyproject's name when it wants to).
"""

from __future__ import annotations

import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
SCANNED = (
    ".agents",
    ".devcontainer",
    ".github",
    "apps",
    "bruno",
    "config",
    "core",
    "docker",
    "docs",
    "scripts",
    "templates",
    "typings",
)
TOP_LEVEL = (
    ".env.example",
    ".pre-commit-config.yaml",
    "AGENTS.md",
    "manage.py",
    "pyproject.toml",
)
PROJECT_NAME = re.compile(r"agentic[\s_-]*django", re.IGNORECASE)
SKIPPED_PARTS = {"__pycache__", "node_modules"}


def _files() -> list[Path]:
    found = [BASE_DIR / name for name in TOP_LEVEL]
    for folder in SCANNED:
        found += [
            path
            for path in (BASE_DIR / folder).rglob("*")
            if path.is_file() and not SKIPPED_PARTS & set(path.parts)
        ]
    return found


def test_no_project_name_outside_the_readme() -> None:
    hits = []
    for path in _files():
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        hits += [
            f"{path.relative_to(BASE_DIR)}:{number}"
            for number, line in enumerate(text.splitlines(), 1)
            if PROJECT_NAME.search(line)
        ]
    assert hits == []
