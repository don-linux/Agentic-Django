"""Bruno scripts keep what they capture in runtime variables, never in a file."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[2]
RULE = "docs/development.md#bruno"
COLLECTION_SUFFIXES = {".yml", ".yaml", ".bru"}
# Bruno 4 writes each of these into an environment or collection file.
PERSISTING_CALL = re.compile(
    r"\bbru\.(?:(?:set|delete)(?:Env|GlobalEnv|Collection)Var"
    r"|deleteAll(?:Env|GlobalEnv|Collection)Vars)\s*\(",
)


def _persisting_calls(source: str) -> list[int]:
    """Return the line of every call in `source` that Bruno saves to disk."""
    return [
        number
        for number, line in enumerate(source.splitlines(), start=1)
        if PERSISTING_CALL.search(line) and not line.lstrip().startswith("//")
    ]


def _scan(root: Path) -> tuple[int, list[str]]:
    """Read bruno/ under `root`: files read, and `path:line` per call."""
    files = sorted(
        path
        for path in (root / "bruno").rglob("*")
        if path.suffix in COLLECTION_SUFFIXES
    )
    found = [
        f"{path.relative_to(root).as_posix()}:{line}"
        for path in files
        for line in _persisting_calls(path.read_text(encoding="utf-8"))
    ]
    return len(files), found


def test_no_bruno_script_writes_a_variable_to_disk() -> None:
    scanned, found = _scan(BASE_DIR)

    assert scanned, "no Bruno file was scanned"
    assert found == [], (
        "These Bruno scripts save a value into a tracked file, where a commit would "
        f"publish it. Keep it in memory with bru.setVar ({RULE}): {found}"
    )


@pytest.mark.parametrize(
    "source",
    [
        'bru.setEnvVar("sessionToken", token);',
        "bru.deleteEnvVar('userId');",
        "bru.deleteAllEnvVars();",
        'bru.setGlobalEnvVar("apiKey", key);',
        'bru.deleteGlobalEnvVar("apiKey");',
        "bru.deleteAllGlobalEnvVars();",
        'bru.setCollectionVar("region", "eu");',
        'bru.deleteCollectionVar("region");',
        "bru.deleteAllCollectionVars();",
        'if (token) bru.setEnvVar ("sessionToken", token);',
    ],
)
def test_the_checker_flags_every_call_that_bruno_saves(source: str) -> None:
    assert _persisting_calls(source) == [1]


@pytest.mark.parametrize(
    "source",
    [
        'bru.setVar("sessionToken", token);',
        'bru.getVar("sessionToken");',
        'bru.deleteVar("userId");',
        "bru.deleteAllVars();",
        'bru.getEnvVar("mailpitUrl");',
        'bru.hasEnvVar("mailpitUrl");',
        "bru.getAllEnvVars();",
        # Comments are not code.
        '// bru.setEnvVar("sessionToken", token);',
    ],
)
def test_the_checker_accepts_runtime_variables_and_reads(source: str) -> None:
    assert _persisting_calls(source) == []


def test_the_scan_reads_only_bruno_and_names_the_file(tmp_path: Path) -> None:
    (tmp_path / "bruno" / "Cuentas").mkdir(parents=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "bruno" / "Cuentas" / "perfil.yml").write_text(
        "runtime:\n"
        "  scripts:\n"
        "    - code: |-\n"
        '        bru.setEnvVar("userId", res.body.id);\n',
    )
    (tmp_path / "bruno" / "Cuentas" / "iniciar-sesion.yml").write_text(
        'code: |-\n  bru.setVar("sessionToken", token);\n',
    )
    # Outside the collection, the docs may name the call they forbid.
    (tmp_path / "docs" / "notes.yml").write_text('bru.setEnvVar("userId", 1)\n')

    assert _scan(tmp_path) == (2, ["bruno/Cuentas/perfil.yml:4"])
