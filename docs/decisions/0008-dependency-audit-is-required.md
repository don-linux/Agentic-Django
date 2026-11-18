# 0008. The dependency audit is a required check

- Status: Accepted
- Date: 2026-11-18

## Context

A known vulnerability in a locked dependency must block a merge, and a failing check tempts people to disable it.

## Decision

`.github/workflows/audit.yml` runs `pip-audit` on the locked versions on pull requests, pushes to `main` and every Monday, and is a required check. A failure is fixed by bumping the pin. An advisory that does not apply is ignored by its ID only, with `--ignore-vuln <ID>`, a reason and an expiry date. See [ci.md#audit](../ci.md#audit).

## Consequences

- The job is never deleted, skipped or dropped from the required checks.
- Django and django-allauth security releases are applied at once.

## Alternatives

- Dependabot alerts alone: rejected, they do not block a merge.
