# 0009. Bruno scripts store values with bru.setVar

- Status: Accepted
- Date: 2026-11-18

## Context

Bruno 4 writes `bru.setEnvVar` values into `bruno/environments/*.yml`, so a smoke run would leave session tokens and ids in tracked files.

## Decision

Scripts store what they capture with `bru.setVar` (runtime variables, kept in memory). Environment files hold only fixed values. `core/tests/test_bruno_scripts.py` fails on `bru.setEnvVar`. See [development.md#bruno](../development.md#bruno).

## Consequences

- A Bruno run leaves `bruno/` unchanged, locally and in CI.

## Alternatives

- `bru.setEnvVar` with a cleanup step: rejected, a failed run would still leave secrets on disk.
