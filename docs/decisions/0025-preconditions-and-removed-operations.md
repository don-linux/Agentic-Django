# 0025. A precondition is a required If-Match; a stale one is a 412, a removed operation a 410

- Status: Accepted
- Date: 2026-11-23

## Context

Two clients that edit the same resource overwrite each other: the second `PATCH` wins even though it was based on data it never saw, the lost update of RFC 9110, section 13.1. A conditional request prevents it: the response carries an `ETag`, and the write sends it back in `If-Match`. RFC 6585 adds 428 for a server that requires the request to be conditional.

An operation that a replacement supersedes is announced with `Deprecation` (RFC 9745), `Sunset` (RFC 8594) and a `Link` to the explanation, and answers 410 Gone once the sunset date has passed ([0024](0024-one-api-on-api-v1.md)).

Every project would write both, and both answer a valid request with a status that depends on state the schema does not describe. schemathesis accepts 409 for that, but not 410 or 412.

## Decision

- **Preconditions ship in core.** `core.concurrency.etag` builds a strong ETag from a row version, the `updated_at` in microseconds or an integer version field. `check_if_match` raises a 412 `precondition_failed` unless `If-Match` names it, or is `*`. The service calls it inside its transaction, after `select_for_update()`.
- **`If-Match` is a required header in the contract.** A write that checks a precondition takes `core.api.schemas.IfMatch`, so a request without the header does not match the schema and is a 400 like any missing parameter. 428 is not used.
- **Deprecation ships in core.** `core.api.deprecation.deprecate` sets the three headers and raises a 410 `gone` after the sunset date. The operation also sets `deprecated=True`.
- **The maps and CORS follow.** `GONE_ERRORS` and `PRECONDITION_ERRORS` declare the statuses, `response_docs(..., etag=True)` the `ETag` header. Browsers may send `If-Match` and read `ETag`, `Deprecation`, `Sunset` and `Link`.
- **The conformance test accepts 410 and 412 for a valid request,** as schemathesis already accepts 409: the generator cannot know a sunset date or the current version.

See [api.md#optimistic-concurrency](../api.md#optimistic-concurrency) and [api.md#deprecating-endpoints-and-fields](../api.md#deprecating-endpoints-and-fields).

## Consequences

- A client generated from the schema knows which writes need `If-Match`, and adding the requirement to an existing write is an oasdiff ERR, as a breaking change should be.
- The ETag names the version of the row the response was built from. A change that does not touch the row, such as a related object, does not change it; a resource whose representation depends on other rows takes an integer version that its services increment.
- No endpoint the template ships uses either helper.

## Alternatives

- **An optional `If-Match` and a 428 when it is missing.** Rejected: the contract would allow a request the server refuses, and adding the header to an existing write would not show as a breaking change.
- **Weak ETags.** Rejected: `If-Match` compares strongly (RFC 9110, section 13.1.1), so a weak tag never matches.
- **An ETag hashed from the body, as Django's `ConditionalGetMiddleware` does.** Rejected: the view has already done all the work, and responses are `no-store`.
- **A 404 after the sunset date.** Rejected: a 410 tells the client that the operation existed and was removed, and the contract keeps the operation ([0024](0024-one-api-on-api-v1.md)).
