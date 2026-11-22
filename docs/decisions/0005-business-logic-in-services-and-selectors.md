# 0005. Business logic lives in services and selectors; DRF stays in api/

- Status: Superseded by 0013
- Date: 2026-11-18

## Context

Business rules spread across serializers, views, the admin and commands are hard to test and are tied to the API framework. A future move to another framework (django-ninja, for example) would then rewrite the rules together with the transport.

## Decision

Writes are services and reads are selectors ([HackSoft Django Styleguide](https://github.com/HackSoftware/Django-Styleguide)): keyword-only, strictly typed functions that take plain values and model instances. Only `apps/<app>/api/`, `core/api/` and `config/api_router.py` import DRF or drf-spectacular; `lint-imports` enforces it. Domain errors are `core.exceptions.ApplicationError`, and the error body is built in `core.errors`, without DRF. See [ARCHITECTURE.md#services-and-selectors](../ARCHITECTURE.md#services-and-selectors), [#strict-typing](../ARCHITECTURE.md#strict-typing) and [#import-contracts](../ARCHITECTURE.md#import-contracts).

## Consequences

- Another API framework replaces `api/`, `core/api/` and `tests/drf/`; services, selectors, their tests and `tests/http/` stay.
- Views, the admin and commands get thinner; a rule that two of them need is written once.
- One more indirection for trivial endpoints.

## Alternatives

- Fat serializers and viewsets (DRF's default style): rejected, the rules would move with the framework.
- Logic in model methods: kept for small invariants only (`clean()`, `save()` normalization); cross-model work belongs in services.
