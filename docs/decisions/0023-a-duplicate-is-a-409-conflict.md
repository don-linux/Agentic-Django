# 0023. A duplicate is a 409 conflict

- Status: Accepted
- Date: 2026-11-23

## Context

`full_clean()` runs a model's unique checks, and the handler turns its `ValidationError` into a 400 `validation_error` with a field error. But a schema cannot say that a value must be unused, so that 400 rejects a request the schema allows. schemathesis sends the same value twice and reports a schema-valid request rejected (ST202). Its positive-data check accepts a 409 for such a request, the status RFC 9110 gives to a conflict with the current state of the resource, but never a 400.

## Decision

- A unique value that is already used is a 409. The service checks it inside its transaction, before `full_clean()`, and raises `ConflictError` with the registered code `already_exists`, with the field in `extra`.
- `full_clean()` stays as the backstop, and so does the database constraint. When two requests pass the check at the same moment, the constraint refuses the second with an `IntegrityError` (a 500).
- An operation that can create a duplicate declares `core.api.responses.CONFLICT_ERRORS`, and so does the delete of a model that `PROTECT` foreign keys point to (`protected`, already a 409).

See [api.md#duplicates](../api.md#duplicates).

## Consequences

- No endpoint the template ships can create a duplicate: users change only `name`, and accounts are created through allauth.
- Clients tell a duplicate from an invalid value by the status and the code (`already_exists`, with `extra.field`), not by a field error.

## Alternatives

- **The 400 of `full_clean()`.** Rejected: the first create endpoint with a unique field would fail the conformance test, which has no exceptions ([0018](0018-the-api-accepts-exactly-what-its-contract-declares.md)).
- **Map every `unique` error of `full_clean()` to 409 in the handler.** Rejected: an expression constraint, such as the case-insensitive email, carries no `unique` code. An explicit check in the service also names the field for the client.
- **Expect 400 in the conformance test.** Rejected: that is an exception for a status the contract can avoid.
