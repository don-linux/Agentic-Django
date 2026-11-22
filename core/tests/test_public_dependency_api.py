"""The project's code, tests included, imports only its dependencies' public API.

A module path with an ``internal`` or ``_private`` component, or an imported
``_name``, is a dependency's private API: it can change in any release without
a deprecation (allauth keeps such helpers under ``*.internal``; Ninja, Django
and Pydantic mark them with a leading underscore). ``importlib.import_module``
and ``__import__`` with a literal module count as imports. The project's code
is config/, apps/, core/, scripts/, manage.py and conftest.py, with their
tests. This check sees imports only; attribute access is not typed here.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[2]
SOURCES = ("config", "apps", "core", "scripts")
ROOT_MODULES = ("manage.py", "conftest.py")
FIRST_PARTY = {*SOURCES, "conftest", "manage", "__future__"}
IMPORT_CALLS = {"import_module", "__import__"}


def _is_private(part: str) -> bool:
    return part == "internal" or (part.startswith("_") and not part.startswith("__"))


def _imported_by_call(node: ast.Call) -> str | None:
    """The literal module of importlib.import_module("m") or __import__("m")."""
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name not in IMPORT_CALLS or not node.args:
        return None
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def _private_imports(source: str) -> list[str]:
    """`module` or `module:name` for every private third-party import in source."""
    found = []
    for node in ast.walk(ast.parse(source)):
        pairs: list[tuple[str, str | None]]
        if isinstance(node, ast.Import):
            pairs = [(alias.name, None) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            pairs = [(node.module, alias.name) for alias in node.names]
        elif isinstance(node, ast.Call) and (module := _imported_by_call(node)):
            pairs = [(module, None)]
        else:
            continue
        for module, name in pairs:
            parts = module.split(".")
            if parts[0] in FIRST_PARTY:
                continue
            if any(_is_private(part) for part in parts):
                found.append(module)
            elif name is not None and _is_private(name):
                found.append(f"{module}:{name}")
    return found


def _project_modules() -> list[Path]:
    return [
        path for source in SOURCES for path in sorted((BASE_DIR / source).rglob("*.py"))
    ] + [BASE_DIR / name for name in ROOT_MODULES]


def test_project_code_uses_only_public_dependency_apis() -> None:
    modules = _project_modules()
    scanned = {str(path.relative_to(BASE_DIR)) for path in modules}
    assert {
        "manage.py",
        "conftest.py",
        "scripts/forbid_cache_clear.py",
        # Tests too: a test that leans on a private helper breaks in any release.
        "apps/users/tests/http/test_rate_limits.py",
        "core/tests/test_cache_backend.py",
    } <= scanned
    found = {
        str(path.relative_to(BASE_DIR)): imports
        for path in modules
        if (imports := _private_imports(path.read_text()))
    }
    assert found == {}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            (
                "from allauth.headless.internal.sessionkit"
                " import authenticate_by_x_session_token"
            ),
            ["allauth.headless.internal.sessionkit"],
        ),
        ("import ninja._compatibility", ["ninja._compatibility"]),
        ("from pydantic import _internal", ["pydantic:_internal"]),
        (
            "from allauth.headless.contrib.ninja.security import x_session_token_auth",
            [],
        ),
        ("from __future__ import annotations\nfrom core.api import _private", []),
        (
            "import importlib\nimportlib.import_module('allauth.headless.internal')",
            ["allauth.headless.internal"],
        ),
        ("__import__('ninja._compatibility')", ["ninja._compatibility"]),
        ("import importlib\nimportlib.import_module('ninja.throttling')", []),
    ],
    ids=[
        "internal-module",
        "underscore-module",
        "underscore-name",
        "public",
        "first-party",
        "import-module-call",
        "dunder-import-call",
        "import-module-call-public",
    ],
)
def test_the_check_tells_private_from_public_imports(
    source: str, expected: list[str]
) -> None:
    assert _private_imports(source) == expected
