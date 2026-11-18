"""
Every docs/<file>.md#<anchor> pointer outside Markdown names a real heading.

lychee checks the Markdown links (docs/ci.md#docs-lint); this covers code,
Compose, scripts and env examples.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[2]
SKIPPED_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "dist",
    "htmlcov",
    "staticfiles",
    "graphify-out",
}
# A smoke floor: the scan must keep finding the pointers the code holds.
MIN_POINTERS = 50
POINTER = re.compile(r"\bdocs/([A-Za-z0-9_./-]+\.md)#([A-Za-z0-9_-]+)")
HEADING = re.compile(r"^#{1,6} +(.+?) *#* *$")


def _sources() -> list[Path]:
    return sorted(
        path
        for path in BASE_DIR.rglob("*")
        if path.is_file()
        and path.suffix != ".md"
        and not SKIPPED_DIRS & set(path.relative_to(BASE_DIR).parts)
        and not any(part.endswith("_cache") for part in path.parts)
        and path.suffix not in {".pyc", ".zip", ".png", ".ico"}
    )


def _pointers() -> list[tuple[str, str, str]]:
    found = set()
    for path in _sources():
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        for doc, anchor in POINTER.findall(text):
            found.add((str(path.relative_to(BASE_DIR)), doc, anchor))
    return sorted(found)


@cache
def _anchors(doc: str) -> frozenset[str]:
    # GitHub's slugs: lower case, punctuation dropped, spaces to "-", "-1" on repeats.
    seen: dict[str, int] = {}
    anchors = set()
    in_fence = False
    for line in (BASE_DIR / "docs" / doc).read_text().splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        match = None if in_fence else HEADING.match(line)
        if match is None:
            continue
        slug = re.sub(r"[^\w\- ]", "", match.group(1).lower()).replace(" ", "-")
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        anchors.add(slug if count == 0 else f"{slug}-{count}")
    return frozenset(anchors)


def test_there_are_pointers_to_check() -> None:
    assert len(_pointers()) > MIN_POINTERS


@pytest.mark.parametrize(("source", "doc", "anchor"), _pointers())
def test_a_docs_pointer_names_a_real_heading(
    source: str, doc: str, anchor: str
) -> None:
    assert (BASE_DIR / "docs" / doc).is_file(), f"{source}: docs/{doc} does not exist"
    assert anchor in _anchors(doc), f"{source}: docs/{doc} has no #{anchor}"
