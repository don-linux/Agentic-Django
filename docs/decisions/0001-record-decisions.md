# 0001. Record decisions in docs/decisions/

- Status: Accepted
- Date: 2026-11-18

## Context

The template is changed by people and by coding agents, often months apart. The rules are in [AGENTS.md](../../AGENTS.md) and the area docs, but the reasons behind a rule get lost, and an agent that does not know them may "simplify" a decision away.

## Decision

Each decision that shapes the template gets a short record in `docs/decisions/`, numbered, in the format of [template.md](template.md), and listed in [README.md](README.md). Records are never rewritten; a new record supersedes an old one.

## Consequences

Before reverting or loosening a rule, read the record that covers it. The [maintain-django-template](../../.agents/skills/maintain-django-template/SKILL.md) skill reads them before an upgrade plan.

## Alternatives

- Reasons inline in AGENTS.md: rejected, AGENTS.md holds mandatory rules and links only.
- Commit messages only: rejected, they are not found when they are needed.
