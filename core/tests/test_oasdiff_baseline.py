"""The temporary YAML-baseline oasdiff ignore hides only the nullable page links.

The api-contract job renames the drf-spectacular components on the base schema,
so the PATCH 400 oneOf branches are compared field by field (docs/api.md).
"""

from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
IGNORE = BASE_DIR / ".github" / "oasdiff-yaml-baseline-err-ignore.txt"
WORKFLOW = BASE_DIR / ".github" / "workflows" / "ci.yml"


def test_the_ignore_file_lists_only_next_and_previous() -> None:
    lines = IGNORE.read_text().splitlines()
    assert [line.split("`")[1] for line in lines] == ["next", "previous"]


def test_the_yaml_baseline_is_renamed_before_the_comparison() -> None:
    workflow = WORKFLOW.read_text()
    assert "s#schemas/ValidationError'#schemas/ValidationErrorSchema'#g" in workflow
    assert "s#schemas/Error'#schemas/ErrorSchema'#g" in workflow
    assert 'BASE_SCHEMA="$RUNNER_TEMP/base-openapi.yaml"' in workflow
