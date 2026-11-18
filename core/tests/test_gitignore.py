"""
The ignore rules keep secrets and editor files out without hiding source files.

See .gitignore.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _ignored(path: str) -> bool:
    git = shutil.which("git")
    if git is None or not (ROOT / ".git").exists():
        pytest.skip("needs a git checkout")
    result = subprocess.run(  # noqa: S603
        [git, "check-ignore", "--quiet", "--no-index", path], cwd=ROOT, check=False
    )
    return result.returncode == 0


@pytest.mark.parametrize(
    "path",
    [
        "core/static/_logo.svg",
        "docs/.diagram.svg",
        "scripts/_restore.sql",
        "apps/tags/models.py",
        "apps/users/api/tags/views.py",
    ],
)
def test_source_files_are_not_ignored(path: str) -> None:
    assert not _ignored(path)


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        "docker/prod/.env",
        "apps/users/.models.py.swp",
        "apps/users/.models.py.swo",
        "tags",
        "media/avatar.png",
        "id_ed25519",
        "deploy/id_dsa",
        "secrets.yaml",
        "credentials.json",
        "config/credentials.json",
        "certs/server.pem",
        "deploy/tls.key",
    ],
)
def test_secrets_and_editor_files_stay_ignored(path: str) -> None:
    assert _ignored(path)
