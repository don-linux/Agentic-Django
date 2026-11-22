"""docs/openapi.json is the official stdout export of export_openapi_schema.

The contract is regenerated with Ninja's command and a shell redirect:

    python manage.py export_openapi_schema --settings config.settings.test \\
        --api config.api.api --indent 2 > docs/openapi.json

Without --output the command writes the schema to stdout (Ninja guide,
"Exporting the schema"), and Django's OutputWrapper ends that output with a
newline (Django, "Writing custom django-admin commands"). end-of-file-fixer
(pre-commit-hooks v6.0.0) wants a non-empty file to end in exactly one newline,
so it leaves the redirected file as generated. --output writes the same JSON
with no final newline: the hook and the drift check in scripts/check.sh lint
would rewrite each other. pre-commit runs the real hook on the file; these
tests pin what makes it a no-op: one final LF and no carriage return.

--api names config.api.api, the instance with every router mounted when its
module is imported, as the guide exports project.urls.api. core.api.api.api only
gets the routes as a side effect of the URLconf import that reverse() triggers.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

from core.tests.production_settings import run_python

BASE_DIR = Path(__file__).resolve().parents[3]
COMMITTED_SCHEMA = BASE_DIR / "docs" / "openapi.json"
EXPORTED_API = "config.api.api"
REGENERATE = (
    "python manage.py export_openapi_schema --settings config.settings.test "
    f"--api {EXPORTED_API} --indent 2 > docs/openapi.json"
)


@pytest.fixture(scope="module")
def redirected_export() -> bytes:
    """The bytes the shell redirect writes: the command's stdout, in its own process."""
    result = subprocess.run(
        [
            sys.executable,
            "manage.py",
            "export_openapi_schema",
            "--api",
            "config.api.api",
            "--indent",
            "2",
        ],
        cwd=BASE_DIR,
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.test"},
        capture_output=True,
        check=True,
    )
    return result.stdout


def test_the_committed_contract_is_the_redirected_export(
    redirected_export: bytes,
) -> None:
    assert COMMITTED_SCHEMA.read_bytes() == redirected_export, (
        f"docs/openapi.json is out of date. Regenerate it with: {REGENERATE}"
    )


def test_the_redirected_export_ends_in_exactly_one_newline(
    redirected_export: bytes,
) -> None:
    assert redirected_export.endswith(b"}\n")
    assert not redirected_export.endswith(b"\n\n")
    assert b"\r" not in redirected_export


def test_the_output_option_writes_no_final_newline(
    tmp_path: Path, redirected_export: bytes
) -> None:
    # Why the contract is not written with --output. If a Ninja upgrade adds the
    # newline there, this fails and --output becomes an option again.
    output = tmp_path / "schema.json"
    call_command(
        "export_openapi_schema",
        "--api",
        EXPORTED_API,
        "--indent",
        "2",
        "--output",
        str(output),
    )
    written = output.read_bytes()
    assert written + b"\n" == redirected_export
    assert not written.endswith(b"\n")


def test_the_in_process_export_is_the_redirected_one(redirected_export: bytes) -> None:
    # check.sh and test_openapi_contract.py read the same stdout in process.
    out = StringIO()
    call_command(
        "export_openapi_schema",
        "--api",
        EXPORTED_API,
        "--indent",
        "2",
        stdout=out,
    )
    assert out.getvalue().encode() == redirected_export


# Run in a fresh process: django.setup(), then import only the named instance.
# get_openapi_schema() with a prefix never calls reverse(), so the URLconf (and
# with it config.api) is not imported as a side effect.
_ROUTES_ON_IMPORT = """
import json
import sys

import django
from django.utils.module_loading import import_string

django.setup()
api = import_string(sys.argv[1])
paths = sorted(api.get_openapi_schema(path_prefix="/api/v1/")["paths"])
print(json.dumps({"paths": paths, "urlconf": "config.urls" in sys.modules}))
"""


def _routes_on_import(dotted_path: str) -> tuple[list[str], bool]:
    result = run_python(
        _ROUTES_ON_IMPORT,
        dotted_path,
        env={"DJANGO_SETTINGS_MODULE": "config.settings.test"},
    )
    found = json.loads(result.stdout.splitlines()[-1])
    return found["paths"], found["urlconf"]


def test_the_exported_instance_has_every_route_when_imported() -> None:
    paths, urlconf_loaded = _routes_on_import(EXPORTED_API)
    assert not urlconf_loaded
    assert paths == sorted(json.loads(COMMITTED_SCHEMA.read_text())["paths"])


def test_the_bare_instance_has_no_route_without_the_urlconf() -> None:
    # Why --api is not core.api.api.api: imported alone, it has no router.
    paths, urlconf_loaded = _routes_on_import("core.api.api.api")
    assert not urlconf_loaded
    assert paths == []


_API_OPTION = re.compile(r"export_openapi_schema\b[^\n]*?--api[ =]([\w.]+)")
_SKIPPED_DIRS = {".git", ".venv", "node_modules", "staticfiles", "graphify-out"}


def test_every_documented_export_names_the_mounted_instance() -> None:
    commands: dict[str, list[str]] = {}
    for path in sorted(BASE_DIR.rglob("*")):
        relative = path.relative_to(BASE_DIR)
        if (
            not path.is_file()
            or path.suffix not in {".md", ".sh", ".yml", ".yaml"}
            or _SKIPPED_DIRS & set(relative.parts)
        ):
            continue
        for match in _API_OPTION.finditer(path.read_text()):
            commands.setdefault(match.group(1), []).append(str(relative))
    assert commands
    assert set(commands) == {EXPORTED_API}, commands
