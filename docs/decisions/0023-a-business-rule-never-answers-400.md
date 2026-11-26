# 0023. A business rule answers 409, 403 or 404, never 400

- Status: Accepted
- Date: 2026-11-23

## Context

A 400 says that the request does not match the contract. A request that matches the schema can still break a rule the schema cannot state: a unique value that is already used, stock that is not enough, an order that already shipped, a caller who may read an object but not change it. schemathesis sends such requests and reports a 400 for any of them as a schema-valid request rejected (ST202). Its positive-data check accepts 401, 403, 404, 409 and 429 for a valid request, the statuses that depend on who asks and on the stored data, but never a 400.

`full_clean()` runs the model's unique checks, and the handler turns its `ValidationError` into a 400 `validation_error` with a field error. `core.exceptions.ApplicationError` defaulted to 400 too.

## Decision

- **A business rule is a 409 conflict by default.** `ApplicationError` defaults to 409, as `ConflictError` does, and refuses `status_code=400` with a `ValueError`, so the mistake shows where the service raises it.
- **403 for a caller who may not, 404 to hide an object.** A service raises `ApplicationError(..., code="permission_denied", status_code=403)` for an object the caller can see but may not change; scoping keeps an invisible one a 404.
- **A unique value that is already used is a 409** with the registered code `already_exists` and the field in `extra`. The service checks it inside its transaction, before `full_clean()`. `full_clean()` stays as the backstop, and so does the database constraint: when two requests pass the check at the same moment, the constraint refuses the second with an `IntegrityError` (a 500).
- An operation that can answer 409 declares `core.api.responses.CONFLICT_ERRORS`, and so does the delete of a model that `PROTECT` foreign keys point to (`protected`, already a 409).

See [api.md#domain-errors](../api.md#domain-errors).

## Consequences

- A 400 always means that the request does not match the contract, and its `fields` say where.
- No endpoint the template ships raises a business rule: users change only `name`, and accounts are created through allauth.
- Clients tell a broken rule from an invalid value by the status and the code (`already_exists`, with `extra.field`), not by a field error.

## Alternatives

- **The 400 of `full_clean()`, and `ApplicationError` at 400.** Rejected: the first endpoint with a unique field or a business rule would fail the conformance test, which has no exceptions ([0018](0018-the-api-accepts-exactly-what-its-contract-declares.md)).
- **422 Unprocessable Content.** Rejected: schemathesis does not accept it for a valid request, and RFC 9110 gives 409 to a conflict with the current state of the resource, which is what a business rule checks.
- **Map every `unique` error of `full_clean()` to 409 in the handler.** Rejected: an expression constraint, such as the case-insensitive email, carries no `unique` code. An explicit check in the service also names the field for the client.
- **Expect 400 in the conformance test.** Rejected: that is an exception for a status the contract can avoid.
