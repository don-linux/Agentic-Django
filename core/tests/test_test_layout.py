"""
Tests are grouped by what they depend on (docs/development.md#test-layout).

Only modules under a tests/ninja/ directory import django-ninja, and
nothing outside tests/ninja/ imports from one. No module of the test tree imports
a test module: helpers that several modules share live in support modules.
"""

from __future__ import annotations

import ast
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

BASE_DIR = Path(__file__).resolve().parents[2]
FRAMEWORKS = frozenset({"ninja"})
LAYERS = ("http", "ninja")


def _layer(path: Path) -> str | None:
    """Return "http" or "ninja" when `path` is under a tests/http/ or tests/ninja/."""
    for parent, child in pairwise(path.parts):
        if parent == "tests" and child in LAYERS:
            return child
    return None


def _imported(source: str) -> list[tuple[int, str]]:
    """
    Return (line, dotted name) for every name an import statement brings in.

    `from a import b` gives `a` and `a.b`, because `b` may be a module. Relative
    imports keep their leading dots.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [(node.lineno, alias.name) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            dot = "" if module.endswith(".") else "."
            found.append((node.lineno, module))
            found += [
                (node.lineno, f"{module}{dot}{alias.name}") for alias in node.names
            ]
    return found


def _framework_imports(source: str) -> list[int]:
    """Return the lines of `source` that import django-ninja."""
    return sorted(
        {
            line
            for line, name in _imported(source)
            if not name.startswith(".") and name.split(".")[0] in FRAMEWORKS
        }
    )


def _ninja_package_imports(source: str) -> list[int]:
    """Return the lines of `source` that import from a tests.ninja package."""
    return sorted(
        {
            line
            for line, name in _imported(source)
            if ("tests", "ninja") in pairwise(name.lstrip(".").split("."))
        }
    )


def _test_module_imports(source: str) -> list[int]:
    """Return the lines of `source` that import a test module (test_*)."""
    return sorted(
        {
            line
            for line, name in _imported(source)
            if any(part.startswith("test_") for part in name.split("."))
        }
    )


def _test_tree(root: Path) -> list[Path]:
    """Every module of the test tree: apps/*/tests/, core/tests/, conftest.py."""
    paths = [
        *root.glob("apps/*/tests/**/*.py"),
        *(root / "core" / "tests").rglob("*.py"),
        root / "conftest.py",
    ]
    return sorted(path for path in paths if path.is_file())


def _scan(
    root: Path, check: Callable[[str], list[int]], *, outside_ninja: bool
) -> tuple[int, list[str]]:
    """Run `check` on the test tree under `root`: files read, `path:line` found."""
    files = [
        path
        for path in _test_tree(root)
        if not outside_ninja or _layer(path.relative_to(root)) != "ninja"
    ]
    found = [
        f"{path.relative_to(root).as_posix()}:{line}"
        for path in files
        for line in check(path.read_text(encoding="utf-8"))
    ]
    return len(files), found


def test_only_tests_ninja_imports_ninja() -> None:
    scanned, found = _scan(BASE_DIR, _framework_imports, outside_ninja=True)

    assert scanned, "no Python file was scanned"
    assert found == [], (
        "These lines import ninja outside tests/ninja/. "
        "An HTTP test uses django.test.Client and the root fixtures; a test of Ninja "
        f"itself goes in tests/ninja/ (docs/development.md): {found}"
    )


def test_nothing_outside_tests_ninja_imports_from_it() -> None:
    scanned, found = _scan(BASE_DIR, _ninja_package_imports, outside_ninja=True)

    assert scanned, "no Python file was scanned"
    assert found == [], (
        "These lines import a tests/ninja/ module from outside tests/ninja/, which "
        f"brings Ninja back in through a helper: {found}"
    )


def test_no_module_of_the_test_tree_imports_a_test_module() -> None:
    scanned, found = _scan(BASE_DIR, _test_module_imports, outside_ninja=False)

    assert scanned, "no Python file was scanned"
    assert found == [], (
        "These lines import a test module. Move the shared helper into a support "
        f"module, such as core/tests/routes.py: {found}"
    )


def test_every_test_carries_the_marker_of_its_directory(
    request: pytest.FixtureRequest,
) -> None:
    wrong = [
        item.nodeid
        for item in request.session.items
        if {marker.name for marker in item.iter_markers()} & set(LAYERS)
        != ({layer} if (layer := _layer(item.path.relative_to(BASE_DIR))) else set())
    ]

    assert wrong == [], f"conftest.py marks these by the wrong directory: {wrong}"


@pytest.mark.parametrize(
    ("path", "layer"),
    [
        ("apps/users/tests/http/test_views.py", "http"),
        ("core/tests/ninja/test_schema.py", "ninja"),
        ("core/tests/ninja/adapters.py", "ninja"),
        ("core/tests/test_net.py", None),
        # An app or a module that is only named http or ninja is no layer.
        ("apps/http/tests/test_views.py", None),
        ("core/tests/ninja_helpers.py", None),
    ],
)
def test_the_layer_comes_from_the_directory(path: str, layer: str | None) -> None:
    assert _layer(Path(path)) == layer


@pytest.mark.parametrize(
    "source",
    [
        "import ninja\n",
        "from ninja import NinjaAPI\n",
        "from ninja.testing import TestClient\n",
        "import ninja.pagination as pagination\n",
        "from ninja.errors import Throttled\n",
        "if TYPE_CHECKING:\n    from ninja import Schema\n",
        "def view():\n    from ninja import Router\n",
    ],
)
def test_the_framework_check_flags_every_way_of_importing_it(source: str) -> None:
    assert _framework_imports(source) != []


@pytest.mark.parametrize(
    "source",
    [
        "from django.test import Client\n",
        # Project code may use Ninja; only what the test module imports counts.
        "from apps.users.api.views import users_list\n",
        "from .ninja import helper\n",
        'NINJA_DEFAULT_THROTTLE_RATES = {"anon": "60/min"}\n',
        '"""Never `from ninja import NinjaAPI` here."""\n',
        'pytest_plugins = ["core.tests.ninja.adapters"]\n',
    ],
)
def test_the_framework_check_accepts_django_and_project_imports(source: str) -> None:
    assert _framework_imports(source) == []


@pytest.mark.parametrize(
    "source",
    [
        "from core.tests.ninja.pagination_views import _list\n",
        "from core.tests import ninja\n",
        "import apps.users.tests.ninja.test_views\n",
    ],
)
def test_the_ninja_package_check_flags_imports_from_tests_ninja(source: str) -> None:
    assert _ninja_package_imports(source) != []


@pytest.mark.parametrize(
    "source",
    [
        # A string names the plugin: loading it is not an import.
        'pytest_plugins = ["core.tests.ninja.adapters"]\n',
        "from apps.users.api.views import users_list\n",
        "from core.tests.routes import _routes\n",
    ],
)
def test_the_ninja_package_check_accepts_other_modules(source: str) -> None:
    assert _ninja_package_imports(source) == []


@pytest.mark.parametrize(
    "source",
    [
        "from core.tests.test_api import _call\n",
        "import apps.users.tests.test_views\n",
        "from core.tests import test_api\n",
        "from . import test_api\n",
        "from .test_api import _call\n",
    ],
)
def test_the_test_import_check_flags_every_way_of_importing_one(source: str) -> None:
    assert _test_module_imports(source) != []


@pytest.mark.parametrize(
    "source",
    [
        "from core.tests.cache_helpers import _login\n",
        "from apps.users.tests.factories import UserFactory\n",
        "from django.test import Client\n",
        "from conftest import ThrottleRates\n",
        'ROOT_URLCONF = "core.tests.ninja.test_schema"\n',
    ],
)
def test_the_test_import_check_accepts_support_modules(source: str) -> None:
    assert _test_module_imports(source) == []


def test_the_scan_reads_the_test_tree_outside_ninja_and_names_the_file(
    tmp_path: Path,
) -> None:
    ninja_import = "import os\n\nfrom ninja.testing import TestClient\n"
    for path, source in {
        "apps/shop/tests/http/test_orders.py": ninja_import,
        "apps/shop/tests/ninja/test_schemas.py": ninja_import,
        "apps/shop/api/views.py": ninja_import,
        "core/tests/test_net.py": "import ninja\n",
        "core/tests/routes.py": "import re\n",
        "conftest.py": "from ninja import Schema\n",
    }.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(source)

    assert _scan(tmp_path, _framework_imports, outside_ninja=True) == (
        4,
        [
            "apps/shop/tests/http/test_orders.py:3",
            "conftest.py:1",
            "core/tests/test_net.py:1",
        ],
    )
