"""Every code in docs/api.md#error-codes is one the application emits.

A documented code nothing raises is a contract an agent would rely on. Rows
raised outside Django (nginx) or by a pattern for future endpoints are skipped.
"""

from __future__ import annotations

import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
SOURCES = ("apps", "config", "core")
OUTSIDE_THE_APP = ("nginx", "Idempotent retries")
_ROW = re.compile(r"^\| `(?P<code>[a-z_]+)` \| \d{3} \|.*\| (?P<raised_by>[^|]+) \|$")


def _documented_codes() -> list[str]:
    text = (BASE_DIR / "docs" / "api.md").read_text()
    table = text.split("## Error codes", 1)[1].split("\n## ", 1)[0]
    return [
        match["code"]
        for line in table.splitlines()
        if (match := _ROW.match(line))
        and not any(marker in match["raised_by"] for marker in OUTSIDE_THE_APP)
    ]


def _application_source() -> str:
    return "\n".join(
        path.read_text()
        for source in SOURCES
        for path in (BASE_DIR / source).rglob("*.py")
        if "tests" not in path.parts
    )


def test_every_documented_error_code_is_emitted() -> None:
    codes = _documented_codes()
    source = _application_source()
    assert "validation_error" in codes
    assert [code for code in codes if f'"{code}"' not in source] == []
