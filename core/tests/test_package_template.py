"""
scripts/package-template.sh refuses an archive with a secret file in it.

See docs/development.md#packaging.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "package-template.sh"
GIT = shutil.which("git")
GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Tests",
    "GIT_AUTHOR_EMAIL": "tests@example.com",
    "GIT_COMMITTER_NAME": "Tests",
    "GIT_COMMITTER_EMAIL": "tests@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
}

pytestmark = pytest.mark.skipif(GIT is None, reason="needs git")


def _package(tmp_path: Path, committed: str) -> subprocess.CompletedProcess[str]:
    assert GIT is not None
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, repo / "scripts" / "package-template.sh")
    secret = repo / committed
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("not a real secret\n")
    for args in (
        ["init", "--quiet"],
        ["add", "--force", "."],
        ["commit", "--quiet", "--no-verify", "-m", "init"],
    ):
        subprocess.run([GIT, *args], cwd=repo, env=GIT_ENV, check=True)  # noqa: S603
    return subprocess.run(  # noqa: S603
        ["bash", str(repo / "scripts" / "package-template.sh")],  # noqa: S607
        cwd=repo,
        env=GIT_ENV,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "committed",
    [
        ".env",
        ".envrc",
        "deploy/id_rsa",
        "id_ed25519.pub",
        "keys/id_ecdsa_ci",
        "secrets.yaml",
        "config/secrets.yml",
        "certs/server.pem",
        "certs/server.key",
        "credentials.json",
    ],
)
def test_a_committed_secret_file_is_refused(tmp_path: Path, committed: str) -> None:
    result = _package(tmp_path, committed)

    assert result.returncode == 1
    assert "Refusing to package" in result.stderr
    assert committed in result.stderr
    assert not list((tmp_path / "repo" / "dist").glob("*.zip"))


def test_a_clean_tree_is_packaged(tmp_path: Path) -> None:
    result = _package(tmp_path, "docs/readme.md")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith(".zip")
