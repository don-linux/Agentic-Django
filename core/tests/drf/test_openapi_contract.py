"""
The API contract: versioned routes, a valid schema, and a committed copy.

Removing or renaming a path, field or enum value in docs/openapi.yaml is a
breaking change and needs /api/v2/ (AGENTS.md, API).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from django.core.management import call_command
from drf_spectacular.generators import SchemaGenerator

from core.tests.production_settings import run_python

COMMITTED_SCHEMA = Path(__file__).resolve().parents[3] / "docs" / "openapi.yaml"
REGENERATE = (
    "python manage.py spectacular --settings config.settings.test "
    "--file docs/openapi.yaml --validate --fail-on-warn"
)
_VERSIONED_PATH = re.compile(r"^/api/v[0-9]+/")


def test_every_api_route_is_versioned() -> None:
    schema = SchemaGenerator().get_schema(request=None, public=True)

    assert schema["paths"]
    assert [path for path in schema["paths"] if not _VERSIONED_PATH.match(path)] == []


def test_schema_validates_without_warnings(tmp_path: Path) -> None:
    call_command(
        "spectacular",
        "--file",
        str(tmp_path / "schema.yaml"),
        "--validate",
        "--fail-on-warn",
    )


def test_committed_schema_matches_the_code(tmp_path: Path) -> None:
    generated = tmp_path / "schema.yaml"
    call_command("spectacular", "--file", str(generated))

    assert generated.read_text() == COMMITTED_SCHEMA.read_text(), (
        f"docs/openapi.yaml is out of date. Regenerate it with: {REGENERATE}"
    )


# Keys that may differ between the committed (test-settings) contract and a
# schema generated with production settings. cookieAuth.name follows
# SESSION_COOKIE_NAME; servers are filled from ALLOWED_HOSTS.
_PRODUCTION_SCHEMA_ALLOW_LIST = frozenset(
    {
        ("servers",),
        ("components", "securitySchemes", "cookieAuth", "name"),
    },
)


def _diff_paths(
    left: Any, right: Any, prefix: tuple[str, ...] = ()
) -> set[tuple[str, ...]]:
    if left == right:
        return set()
    if type(left) is not type(right):
        return {prefix}
    if isinstance(left, dict):
        paths: set[tuple[str, ...]] = set()
        keys = set(left) | set(right)
        for key in keys:
            if key not in left or key not in right:
                paths.add((*prefix, str(key)))
            else:
                paths |= _diff_paths(left[key], right[key], (*prefix, str(key)))
        return paths
    if isinstance(left, list):
        if len(left) != len(right):
            return {prefix}
        paths = set()
        for index, (item, other) in enumerate(zip(left, right, strict=True)):
            paths |= _diff_paths(item, other, (*prefix, str(index)))
        return paths
    return {prefix}


def test_declared_errors_do_not_drift() -> None:
    """Fail if AutoSchema stops declaring the standard errors or headers."""
    schema = SchemaGenerator().get_schema(request=None, public=True)
    listing = schema["paths"]["/api/v1/users/"]["get"]["responses"]
    patch = schema["paths"]["/api/v1/users/{id}/"]["patch"]["responses"]

    assert {"200", "401", "403", "404", "406", "429"} <= set(listing)
    assert {"200", "400", "401", "403", "404", "406", "415", "429"} <= set(patch)
    assert "WWW-Authenticate" in patch["401"]["headers"]
    assert patch["401"]["headers"]["WWW-Authenticate"]["schema"]["type"] == "string"
    assert "Retry-After" in patch["429"]["headers"]
    assert patch["400"]["content"]["application/json"]["schema"]["oneOf"] == [
        {"$ref": "#/components/schemas/ValidationError"},
        {"$ref": "#/components/schemas/Error"},
    ]
    # 405 stays implicit: allowed methods are the operation keys.
    assert "405" not in listing
    assert "405" not in patch


def test_production_schema_differs_only_by_allow_list(tmp_path: Path) -> None:
    """The committed file is generated under test settings; production may drift."""
    produced = tmp_path / "production.yaml"
    result = run_python(
        "import sys\n"
        "from django.core.management import execute_from_command_line\n"
        "execute_from_command_line("
        "['manage.py', 'spectacular', '--file', sys.argv[1], "
        "'--validate', '--fail-on-warn'])\n",
        str(produced),
        env={
            "DJANGO_SETTINGS_MODULE": "config.settings.production",
        },
    )
    assert produced.exists(), result.stderr
    committed = yaml.safe_load(COMMITTED_SCHEMA.read_text())
    production = yaml.safe_load(produced.read_text())
    drifted = _diff_paths(committed, production)
    unexpected = {
        path
        for path in drifted
        if not any(
            path[: len(allowed)] == allowed for allowed in _PRODUCTION_SCHEMA_ALLOW_LIST
        )
    }
    assert unexpected == set(), (
        f"unexpected production schema drift: {sorted(unexpected)}"
    )
    assert production["components"]["securitySchemes"]["cookieAuth"]["name"] == (
        "__Secure-sessionid"
    )
    assert production["servers"][0]["url"] == "https://api.tests.invalid"
