"""Every module the app imports at load time ships in the production image.

docker/prod/Dockerfile installs `uv sync --locked --no-dev`: a package that only
the dev group brings in is missing there, and Django fails at setup. Django also
imports each dotted path of INSTALLED_APPS and MIDDLEWARE at setup, so those
strings count as load-time imports of the settings that production loads.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path
from typing import Any

import pytest

BASE_DIR = Path(__file__).resolve().parents[2]
SOURCES = ("config", "apps", "core")
SETTINGS_DIR = BASE_DIR / "config" / "settings"
# uv.lock marks the package built from the repository root with one of these.
ROOT_SOURCES = ({"virtual": "."}, {"editable": "."})


def _normalize(name: str) -> str:
    # PEP 503: runs of "-", "_" and "." are one "-", in lower case.
    return re.sub(r"[-_.]+", "-", name).lower()


def _read_toml(name: str) -> dict[str, Any]:
    return tomllib.loads((BASE_DIR / name).read_text())


def _root_package(lock: dict[str, Any], project_name: str) -> dict[str, Any]:
    """The lock entry for [project].name, installed from the repository root."""
    roots = [p for p in lock["package"] if p.get("source") in ROOT_SOURCES]
    matching = [p for p in roots if _normalize(p["name"]) == _normalize(project_name)]
    if len(matching) != 1:
        found = sorted(p["name"] for p in roots)
        msg = f"uv.lock has no root package {project_name!r}; roots: {found}"
        raise LookupError(msg)
    return matching[0]


def _production_closure(
    lock: dict[str, Any] | None = None, project: dict[str, Any] | None = None
) -> set[str]:
    """Names uv installs without the dev group: the root's dependencies, with extras."""
    lock = _read_toml("uv.lock") if lock is None else lock
    project = _read_toml("pyproject.toml") if project is None else project
    packages: dict[str, list[dict[str, Any]]] = {}
    for package in lock["package"]:
        packages.setdefault(_normalize(package["name"]), []).append(package)
    root = _root_package(lock, project["project"]["name"])
    pending = list(root.get("dependencies", []))
    seen: set[tuple[str, str]] = set()
    closure: set[str] = set()
    while pending:
        requirement = pending.pop()
        name = _normalize(requirement["name"])
        extras = requirement.get("extra", [])
        for key in [(name, ""), *((name, extra) for extra in extras)]:
            if key in seen:
                continue
            seen.add(key)
            closure.add(name)
            for package in packages[name]:
                if key[1]:
                    pending += package.get("optional-dependencies", {}).get(key[1], [])
                else:
                    pending += package.get("dependencies", [])
    return closure


def _imports_by_path(target: ast.expr) -> bool:
    # Django imports these settings' strings at setup; *_APPS feed INSTALLED_APPS.
    return isinstance(target, ast.Name) and (
        target.id in {"INSTALLED_APPS", "MIDDLEWARE"} or target.id.endswith("_APPS")
    )


def _strings(node: ast.AST) -> set[str]:
    return {
        child.value.split(".")[0]
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    }


def _dotted_path_imports(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(map(_imports_by_path, node.targets)):
            found |= _strings(node.value)
        elif isinstance(node, ast.AugAssign | ast.AnnAssign) and node.value:
            if _imports_by_path(node.target):
                found |= _strings(node.value)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and _imports_by_path(node.func.value)
        ):
            for argument in node.args:
                found |= _strings(argument)
    return found


def _load_time_imports(tree: ast.Module) -> set[str]:
    found = _dotted_path_imports(tree)
    for node in tree.body:
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module.split(".")[0])
    return found


def _production_settings() -> set[Path]:
    """production.py and the settings modules it imports, relative, at load time."""
    found: set[Path] = set()
    pending = [SETTINGS_DIR / "production.py"]
    while pending:
        path = pending.pop()
        if path in found:
            continue
        found.add(path)
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                names = [node.module] if node.module else [a.name for a in node.names]
                pending += [
                    SETTINGS_DIR / f"{name.split('.')[0]}.py"
                    for name in names
                    if (SETTINGS_DIR / f"{name.split('.')[0]}.py").exists()
                ]
    return found


def _app_modules() -> list[Path]:
    production_settings = _production_settings()
    return [
        path
        for source in SOURCES
        for path in sorted((BASE_DIR / source).rglob("*.py"))
        if "tests" not in path.parts
        and (path.parent != SETTINGS_DIR or path in production_settings)
    ]


def _missing_from_production(paths: list[Path], production: set[str]) -> dict[str, str]:
    """Map each load-time import outside the production closure to a file using it."""
    distributions = packages_distributions()
    local = set(SOURCES)
    missing: dict[str, str] = {}
    for path in paths:
        tree = ast.parse(path.read_text(), filename=str(path))
        for module in _load_time_imports(tree):
            if module in local or module in sys.stdlib_module_names:
                continue
            owners = {_normalize(d) for d in distributions.get(module, [])}
            if owners and not owners & production:
                missing[module] = path.name
    return missing


def _lock(*packages: dict[str, Any]) -> dict[str, Any]:
    return {"package": list(packages)}


def test_load_time_imports_are_production_dependencies() -> None:
    assert _missing_from_production(_app_modules(), _production_closure()) == {}


def test_django_stubs_ext_ships_in_production() -> None:
    # config/settings/base.py calls django_stubs_ext.monkeypatch() at load time.
    assert "django-stubs-ext" in _production_closure()


def test_the_closure_reads_the_lock() -> None:
    # Guard the helper itself: Django is a direct production dependency.
    assert "django" in _production_closure()
    assert "mypy" not in _production_closure()


def test_the_root_package_follows_the_project_name() -> None:
    # personalize-django-template renames [project].name; uv.lock follows it.
    lock = _lock(
        {
            "name": "project",
            "source": {"registry": "https://pypi.org/simple"},
            "dependencies": [{"name": "mypy"}],
        },
        {
            "name": "acme-backend",
            "source": {"virtual": "."},
            "dependencies": [{"name": "django"}],
        },
        {"name": "mypy", "source": {"registry": "https://pypi.org/simple"}},
        {"name": "django", "source": {"registry": "https://pypi.org/simple"}},
    )
    project = {"project": {"name": "acme_backend"}}
    assert _production_closure(lock, project) == {"django"}


def test_an_editable_root_is_found_too() -> None:
    lock = _lock(
        {
            "name": "acme-backend",
            "source": {"editable": "."},
            "dependencies": [{"name": "django"}],
        },
        {"name": "django", "source": {"registry": "https://pypi.org/simple"}},
    )
    assert _production_closure(lock, {"project": {"name": "Acme.Backend"}}) == {
        "django"
    }


def test_a_lock_without_the_project_is_refused() -> None:
    lock = _lock({"name": "project", "source": {"virtual": "."}, "dependencies": []})
    with pytest.raises(LookupError, match="acme_backend"):
        _production_closure(lock, {"project": {"name": "acme_backend"}})


def test_extras_of_production_dependencies_ship_in_production(tmp_path: Path) -> None:
    # django-allauth[headless,mfa], psycopg[c] and redis[hiredis] in [project].
    closure = _production_closure()
    assert {"fido2", "qrcode", "pyjwt", "psycopg-c", "hiredis"} <= closure
    module = tmp_path / "mfa.py"
    module.write_text("import fido2\nimport qrcode\nimport hiredis\n")
    assert _missing_from_production([module], closure) == {}


def test_an_extra_of_the_project_itself_is_not_production() -> None:
    # [project.optional-dependencies] s3 is opt-in (DOCKER_PROD_UV_EXTRAS=s3).
    assert "django-storages" not in _production_closure()


def test_a_load_time_import_from_the_dev_group_is_reported(tmp_path: Path) -> None:
    module = tmp_path / "settings.py"
    module.write_text("import django\nimport mypy\n")
    missing = _missing_from_production([module], _production_closure())
    assert missing == {"mypy": "settings.py"}


def test_django_stubs_ext_is_reported_when_only_dev_brings_it(tmp_path: Path) -> None:
    module = tmp_path / "base.py"
    module.write_text("import django_stubs_ext\n\ndjango_stubs_ext.monkeypatch()\n")
    production = _production_closure() - {"django-stubs-ext"}
    assert _missing_from_production([module], production) == {
        "django_stubs_ext": "base.py"
    }


def test_an_import_inside_a_function_is_not_load_time(tmp_path: Path) -> None:
    module = tmp_path / "command.py"
    module.write_text("def handle() -> None:\n    import mypy\n")
    assert _missing_from_production([module], _production_closure()) == {}


def test_a_dev_app_in_production_settings_is_reported(tmp_path: Path) -> None:
    module = tmp_path / "production.py"
    module.write_text(
        'INSTALLED_APPS = ["django.contrib.auth"]\n'
        'INSTALLED_APPS += ["django_extensions"]\n'
        'MIDDLEWARE = [*MIDDLEWARE, "zeal.middleware.zeal_middleware"]\n'
        'THIRD_PARTY_APPS = ["corsheaders"]\n'
        'INSTALLED_APPS.append("factory")\n'
    )
    missing = _missing_from_production([module], _production_closure())
    assert missing == {
        "django_extensions": "production.py",
        "zeal": "production.py",
        "factory": "production.py",
    }


def test_production_apps_and_middleware_are_not_reported(tmp_path: Path) -> None:
    module = tmp_path / "base.py"
    module.write_text(
        'LOCAL_APPS = ["core", "apps.users"]\n'
        'THIRD_PARTY_APPS = ["allauth.mfa", "corsheaders", "ninja", "axes"]\n'
        'MIDDLEWARE = ["whitenoise.middleware.WhiteNoiseMiddleware"]\n'
        'TIME_ZONE = "mypy"\n'
    )
    assert _missing_from_production([module], _production_closure()) == {}


def test_only_the_settings_production_loads_are_scanned() -> None:
    settings = {p.name for p in _app_modules() if p.parent == SETTINGS_DIR}
    assert {"production.py", "base.py", "guards.py"} <= settings
    assert not {"local.py", "test.py"} & settings
