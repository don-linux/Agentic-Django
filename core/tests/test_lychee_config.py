"""lychee checks tracked Markdown, never the local virtualenv (docs/ci.md#docs-lint).

The CI and links jobs pass the glob './**/*.md', and lychee 0.24.2 expands it
without reading .gitignore. With hidden = true, a local .venv (uv's default
project environment) would add every package's Markdown to the scan.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

LYCHEE_CONFIG = Path(__file__).resolve().parents[2] / "lychee.toml"


def _excluded(path: str) -> bool:
    patterns = tomllib.loads(LYCHEE_CONFIG.read_text())["exclude_path"]
    return any(re.search(pattern, path) for pattern in patterns)


@pytest.mark.parametrize(
    "path",
    [
        # One per branch of (^|/): at the start and after a slash.
        ".venv/lib/python3.14/site-packages/ninja/README.md",
        "./.venv/README.md",
    ],
)
def test_the_local_virtualenv_is_not_scanned(path: str) -> None:
    assert _excluded(path)


@pytest.mark.parametrize(
    "path",
    [
        # A hidden directory that is not .venv, and .venv only as a suffix.
        "./.agents/skills/personalize-django-template/SKILL.md",
        "./docs/my.venv/notes.md",
    ],
)
def test_tracked_markdown_is_scanned(path: str) -> None:
    assert not _excluded(path)
