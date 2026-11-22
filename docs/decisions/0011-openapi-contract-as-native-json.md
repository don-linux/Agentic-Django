# 0011. The API contract is versioned as native OpenAPI 3.1.0 JSON

- Status: Accepted
- Date: 2026-11-18
- Supersedes: [0010](0010-versioned-api-and-breaking-changes.md)

## Context

[0010](0010-versioned-api-and-breaking-changes.md) committed the contract as `docs/openapi.yaml` (OpenAPI 3.0.3 from drf-spectacular). Django Ninja 1.8.0 exports OpenAPI 3.1.0 as JSON only (`export_openapi_schema`). A YAML copy would be a second artefact to keep in sync.

## Decision

Every route stays under `/api/v1/`. `docs/openapi.json` is the committed contract (OpenAPI 3.1.0 as Ninja generates it). CI fails when the file drifts from the code, and on a pull request oasdiff fails on a breaking change against the base branch. Component renames that Ninja emits (`Error` → `ErrorSchema`, `User` → `UserOut`, and the like) are treated as non-breaking. An incompatible change goes to `/api/v2/`. See [api.md#breaking-changes](../api.md#breaking-changes).

## Consequences

- Regenerate the contract with `python manage.py export_openapi_schema --settings config.settings.test --api config.api.api --indent 2 > docs/openapi.json`.
- `--api config.api.api` names the instance that has every router mounted once its module is imported, as Ninja's guide exports `project.urls.api` ([Ninja guide, Exporting the schema](https://django-ninja.dev/guide/openapi/)). `core.api.api.api` gets the routes only when something imports the URLconf, which `get_openapi_schema()` does through `reverse()`; `core/tests/ninja/test_openapi_export.py` checks the instance in a process that never imports it.
- The schema goes to stdout and the shell redirects it. Django ends a command's output with a newline, so `docs/openapi.json` ends in exactly one and pre-commit's `end-of-file-fixer` leaves it as generated. `--output` writes the same JSON with no final newline, so the contract is not written with it.
- The first pull request that introduces the JSON contract still compares against `docs/openapi.yaml` on the base branch; later pull requests compare JSON to JSON.
- oasdiff 1.38.0 reports OpenAPI 3.1 typelist forms (`anyOf: [string, null]`) as `response-property-list-of-types-widened` against a 3.0.3 `nullable: true` baseline. That is a document-version difference, not a change in the HTTP contract.

## Alternatives

- Keep a generated YAML file next to the JSON: rejected, it would drift.
- Pin the document to OpenAPI 3.0.3: rejected, Ninja 1.8.0 has no flag for 3.0.3.
