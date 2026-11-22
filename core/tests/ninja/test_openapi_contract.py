"""The API contract: versioned routes and a committed JSON schema."""

from __future__ import annotations

import json
import re
from io import StringIO
from pathlib import Path
from typing import Any

from django.core.management import call_command

from config.api import api
from core.tests.production_settings import run_python

COMMITTED_SCHEMA = Path(__file__).resolve().parents[3] / "docs" / "openapi.json"
REGENERATE = (
    "python manage.py export_openapi_schema --settings config.settings.test "
    "--api config.api.api --indent 2 > docs/openapi.json"
)
_VERSIONED_PATH = re.compile(r"^/api/v[0-9]+/")


def _codes(responses: dict) -> set[str]:
    return {str(code) for code in responses}


def _response(responses: dict, code: str) -> dict:
    if code in responses:
        return responses[code]
    return responses[int(code)]


# Keys that may differ between the committed (test-settings) contract and a
# schema generated with production settings. cookieAuth.name follows
# SESSION_COOKIE_NAME; servers are filled from ALLOWED_HOSTS.
_PRODUCTION_SCHEMA_ALLOW_LIST = frozenset(
    {
        ("servers",),
        ("components", "securitySchemes", "cookieAuth", "name"),
    },
)


def test_every_api_route_is_versioned() -> None:
    schema = api.get_openapi_schema()
    assert schema["paths"]
    assert [path for path in schema["paths"] if not _VERSIONED_PATH.match(path)] == []


def test_committed_schema_matches_the_code() -> None:
    generated = StringIO()
    call_command(
        "export_openapi_schema",
        "--api",
        "config.api.api",
        "--indent",
        "2",
        stdout=generated,
    )
    assert json.loads(generated.getvalue()) == json.loads(
        COMMITTED_SCHEMA.read_text()
    ), f"docs/openapi.json is out of date. Regenerate it with: {REGENERATE}"


def test_declared_errors_do_not_drift() -> None:
    """Fail if the published operations drop documented errors or grow a PUT."""
    schema = api.get_openapi_schema()
    listing = _codes(schema["paths"]["/api/v1/users/"]["get"]["responses"])
    detail = _codes(schema["paths"]["/api/v1/users/{id}/"]["get"]["responses"])
    patch = schema["paths"]["/api/v1/users/{id}/"]["patch"]["responses"]
    patch_codes = _codes(patch)
    me = _codes(schema["paths"]["/api/v1/users/me/"]["get"]["responses"])
    me_patch = _codes(schema["paths"]["/api/v1/users/me/"]["patch"]["responses"])

    assert {"200", "401"} <= listing
    assert {"200", "401", "404"} <= detail
    assert {"200", "400", "401", "404"} <= patch_codes
    assert {"200", "401"} <= me
    assert {"200", "400", "401"} <= me_patch
    body_400 = _response(patch, "400")["content"]["application/json"]["schema"]["oneOf"]
    assert body_400 == [
        {"$ref": "#/components/schemas/ValidationErrorSchema"},
        {"$ref": "#/components/schemas/ErrorSchema"},
    ]
    assert "put" not in schema["paths"]["/api/v1/users/{id}/"]
    assert "put" not in schema["paths"]["/api/v1/users/me/"]
    # 405 stays implicit: allowed methods are the operation keys.
    assert "405" not in listing
    assert "405" not in patch_codes


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


def test_production_schema_differs_only_by_allow_list(tmp_path: Path) -> None:
    """The committed file is generated under test settings; production may drift."""
    produced = tmp_path / "production.json"
    result = run_python(
        "import sys\n"
        "from django.core.management import execute_from_command_line\n"
        "execute_from_command_line(["
        "'manage.py', 'export_openapi_schema', "
        "'--api', 'config.api.api', "
        "'--output', sys.argv[1], '--indent', '2'])\n",
        str(produced),
        env={
            "DJANGO_SETTINGS_MODULE": "config.settings.production",
        },
    )
    assert produced.exists(), result.stderr
    committed = json.loads(COMMITTED_SCHEMA.read_text())
    production = json.loads(produced.read_text())
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
    assert production["servers"][0]["url"] == "https://api.tests.invalid"
    assert production["components"]["securitySchemes"]["cookieAuth"]["name"] == (
        "__Secure-sessionid"
    )
