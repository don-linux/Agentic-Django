"""The API contract: versioned routes and a committed JSON schema."""

from __future__ import annotations

import json
import re
from io import StringIO
from pathlib import Path

from django.core.management import call_command

from config.api import api

COMMITTED_SCHEMA = Path(__file__).resolve().parents[3] / "docs" / "openapi.json"
REGENERATE = (
    "python manage.py export_openapi_schema --settings config.settings.test "
    "--api config.api.api --indent 2 > docs/openapi.json"
)
_VERSIONED_PATH = re.compile(r"^/api/v[0-9]+/")


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
