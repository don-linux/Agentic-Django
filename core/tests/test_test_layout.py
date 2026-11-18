"""
Tests are grouped by what they depend on (docs/development.md#test-layout).

Only modules under a tests/drf/ directory import DRF or drf-spectacular, and
nothing outside tests/drf/ imports from one. No module of the test tree imports
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
FRAMEWORKS = frozenset({"rest_framework", "drf_spectacular"})
LAYERS = ("http", "drf")


def _layer(path: Path) -> str | None:
    """Return "http" or "drf" when `path` is under a tests/http/ or tests/drf/."""
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
    """Return the lines of `source` that import DRF or drf-spectacular."""
    return sorted(
        {
            line
            for line, name in _imported(source)
            if not name.startswith(".") and name.split(".")[0] in FRAMEWORKS
        }
    )


def _drf_package_imports(source: str) -> list[int]:
    """Return the lines of `source` that import from a tests.drf package."""
    return sorted(
        {
            line
            for line, name in _imported(source)
            if ("tests", "drf") in pairwise(name.lstrip(".").split("."))
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
    root: Path, check: Callable[[str], list[int]], *, outside_drf: bool
) -> tuple[int, list[str]]:
    """Run `check` on the test tree under `root`: files read, `path:line` found."""
    files = [
        path
        for path in _test_tree(root)
        if not outside_drf or _layer(path.relative_to(root)) != "drf"
    ]
    found = [
        f"{path.relative_to(root).as_posix()}:{line}"
        for path in files
        for line in check(path.read_text(encoding="utf-8"))
    ]
    return len(files), found


def test_only_tests_drf_imports_drf_or_drf_spectacular() -> None:
    scanned, found = _scan(BASE_DIR, _framework_imports, outside_drf=True)

    assert scanned, "no Python file was scanned"
    assert found == [], (
        "These lines import rest_framework or drf_spectacular outside tests/drf/. "
        "An HTTP test uses django.test.Client and the root fixtures; a test of DRF "
        f"itself goes in tests/drf/ (docs/development.md): {found}"
    )


def test_nothing_outside_tests_drf_imports_from_it() -> None:
    scanned, found = _scan(BASE_DIR, _drf_package_imports, outside_drf=True)

    assert scanned, "no Python file was scanned"
    assert found == [], (
        "These lines import a tests/drf/ module from outside tests/drf/, which "
        f"brings DRF back in through a helper: {found}"
    )


def test_no_module_of_the_test_tree_imports_a_test_module() -> None:
    scanned, found = _scan(BASE_DIR, _test_module_imports, outside_drf=False)

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
        ("core/tests/drf/test_schema.py", "drf"),
        ("core/tests/drf/adapters.py", "drf"),
        ("core/tests/test_net.py", None),
        # An app or a module that is only named http or drf is no layer.
        ("apps/http/tests/test_views.py", None),
        ("core/tests/drf_helpers.py", None),
    ],
)
def test_the_layer_comes_from_the_directory(path: str, layer: str | None) -> None:
    assert _layer(Path(path)) == layer


@pytest.mark.parametrize(
    "source",
    [
        "import rest_framework\n",
        "from rest_framework import status\n",
        "from rest_framework.test import APIClient\n",
        "import drf_spectacular.utils as schema\n",
        "from drf_spectacular.utils import extend_schema\n",
        "if TYPE_CHECKING:\n    from rest_framework.request import Request\n",
        "def view():\n    from rest_framework.views import APIView\n",
    ],
)
def test_the_framework_check_flags_every_way_of_importing_it(source: str) -> None:
    assert _framework_imports(source) != []


@pytest.mark.parametrize(
    "source",
    [
        "from django.test import Client\n",
        # Project code may use DRF; only what the test module imports counts.
        "from apps.users.api.views import UserViewSet\n",
        "from .rest_framework import helper\n",
        'REST_FRAMEWORK = {"NUM_PROXIES": 1}\n',
        '"""Never `from rest_framework import status` here."""\n',
        'pytest_plugins = ["core.tests.drf.adapters"]\n',
    ],
)
def test_the_framework_check_accepts_django_and_project_imports(source: str) -> None:
    assert _framework_imports(source) == []


@pytest.mark.parametrize(
    "source",
    [
        "from core.tests.drf.pagination_views import _list\n",
        "from core.tests import drf\n",
        "import apps.users.tests.drf.test_views\n",
    ],
)
def test_the_drf_package_check_flags_imports_from_tests_drf(source: str) -> None:
    assert _drf_package_imports(source) != []


@pytest.mark.parametrize(
    "source",
    [
        # A string names the plugin: loading it is not an import.
        'pytest_plugins = ["core.tests.drf.adapters"]\n',
        "from apps.users.api.views import UserViewSet\n",
        "from core.tests.routes import _routes\n",
    ],
)
def test_the_drf_package_check_accepts_other_modules(source: str) -> None:
    assert _drf_package_imports(source) == []


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
        'ROOT_URLCONF = "core.tests.drf.test_create_and_destroy"\n',
    ],
)
def test_the_test_import_check_accepts_support_modules(source: str) -> None:
    assert _test_module_imports(source) == []


def test_the_scan_reads_the_test_tree_outside_drf_and_names_the_file(
    tmp_path: Path,
) -> None:
    drf_import = "import os\n\nfrom rest_framework.test import APIClient\n"
    for path, source in {
        "apps/shop/tests/http/test_orders.py": drf_import,
        "apps/shop/tests/drf/test_serializers.py": drf_import,
        "apps/shop/api/views.py": drf_import,
        "core/tests/test_net.py": "import drf_spectacular\n",
        "core/tests/routes.py": "import re\n",
        "conftest.py": "from rest_framework import status\n",
    }.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(source)

    assert _scan(tmp_path, _framework_imports, outside_drf=True) == (
        4,
        [
            "apps/shop/tests/http/test_orders.py:3",
            "conftest.py:1",
            "core/tests/test_net.py:1",
        ],
    )
