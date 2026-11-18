# ruff: noqa: INP001
"""
Fail on a real `cache.clear()` call outside conftest.py.

On Valkey, clear() is FLUSHDB: it wipes sessions, rate limits and throttles,
whatever the key prefix. The check parses the code, so a comment, a docstring or
a string that names the call passes; a call such as `cache.clear()`,
`caches["default"].clear()` or `self.cache.clear()` fails.

    python scripts/forbid_cache_clear.py apps core config
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ALLOWED_FILE = "conftest.py"


def _receiver_name(node: ast.expr) -> str | None:
    # `caches["default"]` is judged by `caches`.
    if isinstance(node, ast.Subscript):
        node = node.value
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_cache_clear(node: ast.Call) -> bool:
    if not (isinstance(node.func, ast.Attribute) and node.func.attr == "clear"):
        return False
    name = _receiver_name(node.func.value)
    return name is not None and name.endswith(("cache", "caches"))


def find_calls(paths: list[Path]) -> list[str]:
    hits: list[str] = []
    for root in paths:
        files = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for path in files:
            if path.name == ALLOWED_FILE:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            hits.extend(
                f"{path}:{node.lineno}: {ast.unparse(node)}"
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and _is_cache_clear(node)
            )
    return hits


def main() -> int:
    hits = find_calls([Path(arg) for arg in sys.argv[1:]])
    if hits:
        sys.stdout.write("".join(f"{hit}\n" for hit in hits))
        sys.stderr.write(
            "cache.clear() is only allowed in conftest.py: "
            "invalidate with core.cache.invalidate().\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
