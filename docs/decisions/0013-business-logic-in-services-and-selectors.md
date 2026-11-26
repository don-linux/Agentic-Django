# 0013. Business logic lives in services and selectors; the API framework stays in api/

- Status: Accepted
- Date: 2026-11-18
- Supersedes: [0005](0005-business-logic-in-services-and-selectors.md)

## Context

[0005](0005-business-logic-in-services-and-selectors.md) placed writes in services and reads in selectors, and confined the API framework to `api/`. Its wording named Django REST Framework, `config/api_router.py` and `tests/drf/`. The API framework is Django Ninja.

## Decision

Writes are services and reads are selectors ([HackSoft Django Styleguide](https://github.com/HackSoftware/Django-Styleguide)): keyword-only, strictly typed functions that take plain values and model instances. Only `apps/<app>/api/`, `core/api/` and the tests under `tests/ninja/` import Django Ninja; routers are mounted in `config/api.py`. `lint-imports` enforces it. Domain errors are `core.exceptions.ApplicationError`, and the error body is built in `core.errors`, without the API framework. See [ARCHITECTURE.md#services-and-selectors](../ARCHITECTURE.md#services-and-selectors), [#strict-typing](../ARCHITECTURE.md#strict-typing) and [#import-contracts](../ARCHITECTURE.md#import-contracts).

## Consequences

- Another API framework replaces `api/`, `core/api/` and `tests/ninja/`; services, selectors, their tests and `tests/http/` stay.
- Views, the admin and commands get thinner; a rule that two of them need is written once.
- One more indirection for trivial endpoints.

## Alternatives

- Fat views and transport schemas (the default style of an API framework): rejected, the rules would move with the framework.
- Logic in model methods: kept for small invariants only (`clean()`, `save()` normalization); cross-model work belongs in services.
