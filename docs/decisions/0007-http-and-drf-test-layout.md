# 0007. Tests split into http/ and drf/

- Status: Superseded by 0012
- Date: 2026-11-18

## Context

Tests that import DRF break when the API framework changes, even when clients would see no difference. The tests that prove the contract must survive such a change unchanged.

## Decision

`tests/http/` holds the HTTP contract: literal paths through `django.test.Client`, no DRF import. `tests/drf/` holds tests of DRF and drf-spectacular themselves. Everything else is framework-neutral. `core/tests/test_test_layout.py` enforces it, and `conftest.py` marks tests `http` or `drf` by path. See [development.md#test-layout](../development.md#test-layout).

## Consequences

- An assertion under `tests/http/` is never changed to make a test pass after a framework or dependency change; only the client or the setup may change. A failure there is a change in the contract.
- A framework change rewrites `tests/drf/` and `core/tests/drf/adapters.py`.

## Alternatives

- DRF's `APIClient` everywhere: rejected, it ties every API test to DRF.
