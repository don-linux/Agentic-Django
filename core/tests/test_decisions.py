"""
Every decision record is numbered once, has a status, and is in the index.

See docs/decisions/README.md.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DECISIONS = Path(__file__).resolve().parents[2] / "docs" / "decisions"
RECORD = re.compile(r"(\d{4})-[a-z0-9-]+\.md")
RECORDS = sorted(path for path in DECISIONS.glob("*.md") if RECORD.fullmatch(path.name))
STATUS = re.compile(
    r"^- Status: (Proposed|Accepted|Superseded by \d{4})$", re.MULTILINE
)


def test_records_are_numbered_from_0001_without_gaps() -> None:
    numbers = [int(RECORD.fullmatch(path.name).group(1)) for path in RECORDS]  # type: ignore[union-attr]

    assert numbers == list(range(1, len(numbers) + 1))


@pytest.mark.parametrize("record", RECORDS, ids=lambda path: path.stem)
def test_a_record_has_a_title_with_its_number_and_a_status(record: Path) -> None:
    text = record.read_text()

    assert text.startswith(f"# {record.name[:4]}. ")
    assert STATUS.search(text), f"{record.name} has no '- Status:' line"


@pytest.mark.parametrize("record", RECORDS, ids=lambda path: path.stem)
def test_the_index_lists_the_record(record: Path) -> None:
    assert f"]({record.name})" in (DECISIONS / "README.md").read_text()
