"""
Services and selectors take keyword-only arguments and know nothing of HTTP.

Another API framework calls them as they are. See
docs/ARCHITECTURE.md#services-and-selectors.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[2]
LAYERS = ("services", "selectors")
# Transport objects: the API layer turns them into plain values first.
HTTP_MODULES = ("django.http", "ninja")
HTTP_PARAMETERS = frozenset({"request", "serializer", "validated_data", "response"})


def _layer_modules(root: Path) -> list[Path]:
    """apps/<app>/services.py and selectors.py, or the modules of such a package."""
    paths: list[Path] = []
    for layer in LAYERS:
        paths += root.glob(f"apps/*/{layer}.py")
        paths += root.glob(f"apps/*/{layer}/*.py")
    return sorted(paths)


def _function_violations(tree: ast.Module) -> list[str]:
    found = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if node.name.startswith("_"):
            continue
        arguments = node.args
        if arguments.posonlyargs or arguments.args or arguments.vararg:
            found.append(f"{node.lineno}: {node.name} takes positional arguments")
        names = {argument.arg for argument in arguments.kwonlyargs}
        if arguments.kwarg is not None:
            names.add(arguments.kwarg.arg)
        if names & HTTP_PARAMETERS:
            found.append(f"{node.lineno}: {node.name} takes a transport object")
    return found


def _import_violations(tree: ast.Module) -> list[str]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = [node.module or ""]
        else:
            continue
        if any(
            module == banned or module.startswith(f"{banned}.")
            for module in modules
            for banned in HTTP_MODULES
        ):
            found.append(f"{node.lineno}: imports {', '.join(modules)}")
    return found


def _violations(source: str) -> list[str]:
    """Return `line: reason` for every public function or import that breaks a rule."""
    tree = ast.parse(source)
    return _function_violations(tree) + _import_violations(tree)


def test_services_and_selectors_follow_the_conventions() -> None:
    modules = _layer_modules(BASE_DIR)
    found = [
        f"{path.relative_to(BASE_DIR).as_posix()}:{violation}"
        for path in modules
        for violation in _violations(path.read_text(encoding="utf-8"))
    ]

    assert modules, "no services.py or selectors.py was found"
    assert found == [], (
        "Services and selectors take keyword-only plain values and model "
        "instances, never a request, a serializer or a response "
        f"(docs/ARCHITECTURE.md#services-and-selectors): {found}"
    )


@pytest.mark.parametrize(
    "source",
    [
        "def user_update(user, name):\n    pass\n",
        "def user_update(user, /, *, name):\n    pass\n",
        "def user_list(*args):\n    pass\n",
        "def user_update(*, request, name):\n    pass\n",
        "def user_create(*, validated_data):\n    pass\n",
        "async def user_get(public_id):\n    pass\n",
        "from django.http import HttpRequest\n",
        "import ninja.pagination\n",
        "def f():\n    from ninja import Schema\n",
    ],
)
def test_the_checker_flags_each_broken_rule(source: str) -> None:
    assert _violations(source) != []


@pytest.mark.parametrize(
    "source",
    [
        "def user_update(*, user, name):\n    pass\n",
        "def user_list(*, user, filters=None):\n    pass\n",
        "def _delete_sessions(user):\n    pass\n",
        "def user_get(*, public_id, **options):\n    pass\n",
        "from django.db import transaction\n",
        "from apps.users.models import User\n",
        "from core.exceptions import ApplicationError\n",
        "class Helper:\n    def method(self, request):\n        pass\n",
    ],
)
def test_the_checker_accepts_keyword_only_plain_functions(source: str) -> None:
    assert _violations(source) == []


def test_the_scan_finds_modules_and_packages(tmp_path: Path) -> None:
    for path in (
        "apps/shop/services.py",
        "apps/shop/selectors/orders.py",
        "apps/shop/api/views.py",
        "core/services.py",
    ):
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text("")

    assert [p.relative_to(tmp_path).as_posix() for p in _layer_modules(tmp_path)] == [
        "apps/shop/selectors/orders.py",
        "apps/shop/services.py",
    ]
