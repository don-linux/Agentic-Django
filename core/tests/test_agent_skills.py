"""
Every agent skill names itself, says when to use it, and is listed in AGENTS.md.

See AGENTS.md (Skills).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

BASE_DIR = Path(__file__).resolve().parents[2]
SKILLS = sorted((BASE_DIR / ".agents" / "skills").glob("*/SKILL.md"))
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
# A smoke floor: the glob must keep finding the skills every copy keeps
# (graphify is optional).
MIN_SKILLS = 3


def _skills_section() -> str:
    agents = (BASE_DIR / "AGENTS.md").read_text()
    return agents.split("\n## Skills\n", 1)[1].split("\n## ", 1)[0]


def test_the_skills_are_found() -> None:
    assert len(SKILLS) >= MIN_SKILLS


@pytest.mark.parametrize("skill", SKILLS, ids=lambda path: path.parent.name)
def test_a_skill_names_itself_and_says_when_to_use_it(skill: Path) -> None:
    match = FRONTMATTER.match(skill.read_text())
    assert match, f"{skill} has no frontmatter"
    meta = yaml.safe_load(match.group(1))

    assert meta["name"] == skill.parent.name
    assert "Use when" in meta["description"]


@pytest.mark.parametrize("skill", SKILLS, ids=lambda path: path.parent.name)
def test_agents_md_lists_the_skill(skill: Path) -> None:
    assert f".agents/skills/{skill.parent.name}" in _skills_section()
