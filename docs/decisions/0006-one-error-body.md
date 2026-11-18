# 0006. One error body, {"code", "detail"}, not RFC 9457

- Status: Accepted
- Date: 2026-11-18

## Context

Clients need to branch on errors without parsing English text, and every error should look the same whatever raised it.

## Decision

Every `/api/v1/` error, and every request that never reaches a view, answers `{"code", "detail"}` with a stable snake_case `code`, plus `fields` for validation errors and `extra` for some domain errors. The body is built in `core.errors`. Codes are listed in [api.md#error-codes](../api.md#error-codes) and never renamed. `/_allauth/` keeps allauth's own format. See [api.md#error-body](../api.md#error-body) and [#why-not-rfc-9457](../api.md#why-not-rfc-9457).

## Consequences

- A new code is added to the table in the same pull request.
- `tests/http/` pins the body, so another framework must produce the same one.

## Alternatives

- RFC 9457 `application/problem+json`: rejected for now; it would break clients matching `application/json`, and its members can be added later without breaking anyone.
