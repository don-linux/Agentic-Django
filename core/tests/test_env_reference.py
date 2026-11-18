"""docs/environment/ lists every variable the examples hold and the code reads."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[2]
DOCS = BASE_DIR / "docs/environment"
# A key line in an example, set or commented out: KEY=value or # KEY=value.
EXAMPLE_KEY = re.compile(r"^(?:# ?)?([A-Z][A-Z0-9_]*)=", re.MULTILINE)
TABLE_NAME = re.compile(r"^\| `([A-Z][A-Z0-9_]*)` \|", re.MULTILINE)
COMPOSE_VARIABLE = re.compile(r"(?<!\$)\$\{([A-Z][A-Z0-9_]*)")
SHELL_VARIABLE = re.compile(r"\$\{?([A-Z][A-Z0-9_]*)")
ENV_READERS = ("os.environ.get", "os.getenv", "os.environ.setdefault", "_int_env")


def _example_keys(path: str) -> set[str]:
    return set(EXAMPLE_KEY.findall((BASE_DIR / path).read_text()))


def _section_names(doc: str, heading: str) -> set[str]:
    # The table rows under a "## " heading, up to the next one; "### " subsections
    # belong to it.
    text = (DOCS / doc).read_text()
    start = text.index(f"\n## {heading}\n")
    end = text.find("\n## ", start + 1)
    return set(TABLE_NAME.findall(text[start : end if end != -1 else None]))


def _documented() -> set[str]:
    names: set[str] = set()
    for doc in ("development.md", "production.md"):
        names |= set(TABLE_NAME.findall((DOCS / doc).read_text()))
    return names


@pytest.mark.parametrize(
    ("doc", "heading", "example"),
    [
        ("development.md", "Root `.env`", ".env.example"),
        ("development.md", "`docker/dev/.env`", "docker/dev/.env.example"),
        ("production.md", "`docker/prod/.env`", "docker/prod/.env.example"),
    ],
)
def test_each_example_matches_its_reference(
    doc: str, heading: str, example: str
) -> None:
    documented = _section_names(doc, heading)
    keys = _example_keys(example)

    assert keys - documented == set(), f"in {example}, missing from {doc}"
    assert documented - keys == set(), f"in {doc} under {heading}, not in {example}"


def _read_by_python() -> set[str]:
    names: set[str] = set()
    paths = [BASE_DIR / "manage.py", BASE_DIR / "docker/prod/gunicorn.conf.py"]
    for folder in ("config", "core", "apps", "scripts"):
        paths += (BASE_DIR / folder).rglob("*.py")
    for path in paths:
        if "tests" in path.parts or "migrations" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Subscript)
                and ast.unparse(node.value) == "os.environ"
            ):
                key = node.slice
            elif isinstance(node, ast.Call) and node.args:
                function = ast.unparse(node.func)
                if not (function.startswith("env") or function in ENV_READERS):
                    continue
                key = node.args[0]
            else:
                continue
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                names.add(key.value)
    return {name for name in names if name.isupper()}


def _read_by_compose_and_scripts() -> set[str]:
    names: set[str] = set()
    for path in ("docker/dev/compose.yaml", "docker/prod/compose.yaml"):
        names |= set(COMPOSE_VARIABLE.findall((BASE_DIR / path).read_text()))
    for script in (BASE_DIR / "docker/prod").glob("*.sh"):
        names |= set(SHELL_VARIABLE.findall(script.read_text()))
    return names


def test_every_variable_the_stack_reads_is_documented() -> None:
    read = _read_by_python() | _read_by_compose_and_scripts()

    # The scan must see the code, or the check proves nothing.
    assert {"DJANGO_SECRET_KEY", "DJANGO_ENV_FILE", "HOST_UID"} <= read
    assert read - _documented() == set()
