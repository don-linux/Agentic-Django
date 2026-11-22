"""scripts/check.sh openapi: the docs/openapi.yaml drift check that ends `lint`.

The export goes to a private mktemp directory, never to a fixed $TMPDIR path:
parallel runs (one per commit, one per user) must not read each other's file.
"""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]


def _check_openapi(tmpdir: Path, **env: str) -> subprocess.CompletedProcess[str]:
    path = f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}"
    return subprocess.run(
        ["/usr/bin/env", "bash", "scripts/check.sh", "openapi"],
        cwd=BASE_DIR,
        env={**os.environ, "TMPDIR": str(tmpdir), "PATH": path, **env},
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_file_at_the_old_fixed_path_is_neither_read_nor_replaced(
    tmp_path: Path,
) -> None:
    # The previous fixed path, $TMPDIR/openapi-check.yaml: something else
    # (here a directory) at that path made every run fail.
    (tmp_path / "openapi-check.yaml").mkdir()

    result = _check_openapi(tmp_path)

    assert result.returncode == 0, result.stderr
    assert [p.name for p in tmp_path.iterdir()] == ["openapi-check.yaml"]


def test_parallel_checks_pass_and_leave_nothing_behind(tmp_path: Path) -> None:
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _check_openapi(tmp_path), range(2)))

    assert [r.returncode for r in results] == [0, 0], [r.stderr for r in results]
    assert list(tmp_path.iterdir()) == []


def test_a_failed_export_fails_the_check_and_leaves_nothing_behind(
    tmp_path: Path,
) -> None:
    result = _check_openapi(tmp_path, DJANGO_SETTINGS_MODULE="config.settings.missing")

    assert result.returncode != 0
    assert "No module named 'config.settings.missing'" in result.stderr
    assert list(tmp_path.iterdir()) == []
