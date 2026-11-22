# 0010. The API is versioned under /api/v1/; a breaking change needs /api/v2/

- Status: Superseded by 0011
- Date: 2026-11-18

## Context

App clients update slowly, so a released field or path must keep working. Unversioned routes leave no room for an incompatible change.

## Decision

Every route is under `/api/v1/`, and `docs/openapi.yaml` is the committed contract. CI fails when the file drifts from the code, and on a pull request oasdiff fails on a breaking change against the base branch. An incompatible change goes to `/api/v2/`. See [api.md#breaking-changes](../api.md#breaking-changes) and [#deprecating-endpoints-and-fields](../api.md#deprecating-endpoints-and-fields).

## Consequences

- Removing or renaming a path, field or enum value needs a new version, or a deprecation period first.
- A change of API framework is checked the same way: the new schema is compared with the last committed one.

## Alternatives

- Unversioned `/api/`: rejected, see the context.
