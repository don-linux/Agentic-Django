"""scripts/forbid_cache_clear.py flags real cache.clear() calls, not their mention."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[2]
SCRIPT = BASE_DIR / "scripts/forbid_cache_clear.py"


def _run(*paths: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), *map(str, paths)],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "source",
    [
        "# cache.clear() is forbidden outside conftest.py.\n",
        '"""Never call cache.clear(): it is FLUSHDB."""\n',
        "MESSAGE = \"caches['default'].clear()\"\n",
        "cache_keys = set()\ncache_keys.clear()\n",
        "items = []\nitems.clear()\n",
    ],
)
def test_a_mention_or_another_clear_passes(tmp_path: Path, source: str) -> None:
    (tmp_path / "module.py").write_text(source)

    result = _run(tmp_path)

    assert result.returncode == 0
    assert result.stdout == ""


@pytest.mark.parametrize(
    "source",
    [
        "from django.core.cache import cache\ncache.clear()\n",
        "from django.core.cache import caches\ncaches['default'].clear()\n",
        "class A:\n    def run(self):\n        self.cache.clear()\n",
        "def run(local_cache):\n    local_cache.clear()\n",
    ],
)
def test_a_real_call_fails(tmp_path: Path, source: str) -> None:
    module = tmp_path / "pkg" / "module.py"
    module.parent.mkdir()
    module.write_text(source)

    result = _run(tmp_path)

    assert result.returncode == 1
    assert result.stdout.startswith(f"{module}:")
    assert ".clear()" in result.stdout
    assert "core.cache.invalidate()" in result.stderr


def test_conftest_may_clear_the_test_caches(tmp_path: Path) -> None:
    (tmp_path / "conftest.py").write_text(
        "from django.core.cache import cache\ncache.clear()\n"
    )

    assert _run(tmp_path).returncode == 0


def test_the_repository_has_no_call_outside_conftest() -> None:
    result = _run(BASE_DIR / "apps", BASE_DIR / "core", BASE_DIR / "config")

    assert result.returncode == 0, result.stdout
