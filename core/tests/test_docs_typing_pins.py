"""
docs/typing.md names the test that pins each undocumented Ninja behaviour.

A row that says "`<path>` pins it" must point at a test file that exists, so a
renamed or deleted pin shows up here instead of as a stale claim in the docs.
"""

from __future__ import annotations

import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
TYPING_DOC = BASE_DIR / "docs" / "typing.md"
NAMED_PIN = re.compile(r"`((?:apps|core)/[\w/]+/test_\w+\.py)` pins it")


def _named_pins(text: str) -> list[str]:
    return NAMED_PIN.findall(text)


def test_the_throttle_order_row_names_its_pin() -> None:
    assert "core/tests/ninja/test_throttle_call_order.py" in _named_pins(
        TYPING_DOC.read_text()
    )


def test_every_named_pin_exists() -> None:
    pins = _named_pins(TYPING_DOC.read_text())
    assert pins
    assert [pin for pin in pins if not (BASE_DIR / pin).is_file()] == []


def test_a_row_without_a_path_names_no_pin() -> None:
    row = "Ninja does not document that order and no test pins it."
    assert _named_pins(row) == []
