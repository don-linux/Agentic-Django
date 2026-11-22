# 0012. Tests split into http/ and ninja/

- Status: Accepted
- Date: 2026-11-18
- Supersedes: [0007](0007-http-and-drf-test-layout.md)

## Context

[0007](0007-http-and-drf-test-layout.md) split the HTTP contract (`tests/http/`) from tests of DRF itself (`tests/drf/`). The API layer now uses Django Ninja, so the framework layer needs a new home. Assertions under `tests/http/` must still survive the change unchanged.

## Decision

`tests/http/` holds the HTTP contract: literal paths through `django.test.Client`, no Ninja import. `tests/ninja/` holds tests of Django Ninja itself. Everything else is framework-neutral. `core/tests/test_test_layout.py` enforces it, and `conftest.py` marks tests `http` or `ninja` by path. See [development.md#test-layout](../development.md#test-layout).

## Consequences

- An assertion under `tests/http/` is never changed to make a test pass after a framework or dependency change; only the client or the setup may change. A failure there is a change in the contract.
- A later framework change rewrites `tests/ninja/` and `core/tests/ninja/adapters.py`.
