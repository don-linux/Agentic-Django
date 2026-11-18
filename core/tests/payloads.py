"""Request bodies shared by the parser tests."""

from __future__ import annotations

# Deeper than the JSON parser's recursion limit.
NESTED_TOO_DEEPLY = "[" * 200_000 + "]" * 200_000
