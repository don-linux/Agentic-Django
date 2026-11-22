"""
Every model field is annotated with its django-stubs generic type.

pyright does not run the django-stubs mypy plugin, so an unannotated
`name = CharField(...)` is Unknown to it. mypy's plugin checks that the
annotation matches the field (null=True needs `| None`). See
docs/typing.md#model-field-annotations.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[2]
FIELD_SUFFIXES = ("Field", "ForeignKey", "OneToOneField", "ManyToManyField")


def _model_modules(root: Path) -> list[Path]:
    paths = [*root.glob("apps/*/models.py"), *root.glob("apps/*/models/*.py")]
    paths.append(root / "core" / "models.py")
    return sorted(path for path in paths if path.exists())


def _callee_name(node: ast.expr) -> str:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def unannotated_fields(tree: ast.Module) -> list[str]:
    found = []
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        for node in cls.body:
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            if _callee_name(node.value.func).endswith(FIELD_SUFFIXES):
                names = ", ".join(ast.unparse(target) for target in node.targets)
                found.append(f"{node.lineno}: {cls.name}.{names}")
    return found


@pytest.mark.parametrize(
    "path", _model_modules(BASE_DIR), ids=lambda path: str(path.relative_to(BASE_DIR))
)
def test_model_fields_are_annotated(path: Path) -> None:
    assert unannotated_fields(ast.parse(path.read_text())) == []


def test_an_unannotated_field_is_reported() -> None:
    source = "class A(Model):\n    name = models.CharField(max_length=1)\n"
    assert unannotated_fields(ast.parse(source)) == ["2: A.name"]


def test_an_annotated_field_passes() -> None:
    source = (
        "class A(Model):\n"
        "    name: models.CharField[str, str] = models.CharField(max_length=1)\n"
    )
    assert unannotated_fields(ast.parse(source)) == []
