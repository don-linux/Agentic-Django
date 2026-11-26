# 0021. Contract details the template keeps, and why

- Status: Accepted
- Date: 2026-11-23

## Context

Several behaviors of the API are not the framework's default, and without a reason they look arbitrary:

- the pagination envelope;
- the optional `PATCH` body;
- the datetime format;
- the JSON bytes;
- the error body and the 405;
- the rollback of handled errors;
- the parser;
- the 401 for a write without credentials;
- throttling and the client IP;
- the `url` field.

This record gives each one its reason, so nobody removes it as a leftover.

## Decision

Each is kept for the reason given:

- **Pagination envelope.** `count`, `next`, `previous` and `results`, with absolute links, 20 per page and at most 100.
  - Ninja's native `{items, count}` is a breaking change: oasdiff reports 3 ERR (`response-required-property-removed`).
  - A page past the last one, or deeper than `DJANGO_API_MAX_PAGE_NUMBER`, is a 404, because every page costs a `COUNT(*)` and an `OFFSET`.
- **Optional `PATCH` body.** An empty `PATCH` changes nothing, as `{}` does. A required body is an oasdiff ERR (`request-body-became-required`), so Ninja's one-item `allOf` and oasdiff's `--flatten-allof` are needed.
- **`UTCDateTime`.** Output is UTC with `Z`. Microseconds are kept, so a client can send a timestamp back exactly, for example in a filter; Ninja's encoder would cut them to milliseconds. Input is exact RFC 3339 with a time zone ([0022](0022-request-fields-validate-strictly.md)).
- **JSON bytes.**
  - The body is compact UTF-8 with no `\u` escapes; RFC 8259, section 8.1, makes UTF-8 the encoding. Error bodies built outside Ninja use the same bytes (`core.errors.json_error`). The schema document is Ninja's own view ([0019](0019-the-schema-and-docs-are-ninja-s-own-views.md)), with Django's default separators.
  - The Content-Type is `application/json` without `charset`, which RFC 8259, section 11, does not define.
- **Error body and the 405.**
  - The body is `{code, detail}` plus `fields` with `non_field_errors`, or `extra` ([0006](0006-one-error-body.md)).
  - The 405 is a JSON error with `Allow`, in the order of Django's `View.http_method_names`.
- **Request handling.**
  - HEAD runs the GET operation, as Django's `View` does.
  - With `ATOMIC_REQUESTS`, a handled error rolls the request back (`set_rollback`): the handler returns a response, so the transaction would otherwise commit.
- **Parser.** It reads UTF-8 only and rejects NaN and Infinity, as RFC 8259, sections 8.1 and 6, require, including a number too large for a double, such as `1e400`, which would become infinity. The renderer refuses them too (`allow_nan=False`): a non-finite float in a response is a 500, never a body with a bare `NaN` token. Another `Content-Type` with a body is 415; Ninja does not inspect it.
- **Authentication and throttling.**
  - A write without valid credentials is 401, not a CSRF 403, as AGENTS.md requires.
  - `ScopedThrottle` counts per user, or per client IP when anonymous.
  - The client IP follows the Django setting `TRUSTED_PROXY_COUNT`, shared with django-axes.
- **Representation.**
  - `url` uses `reverse()`, which keeps the script prefix.
  - Unknown query parameters are ignored: OpenAPI does not forbid them, and Ninja ignores them too.

## Consequences

- The comments and docs that describe these behaviors give these reasons.
- Changing one is a contract decision with its own record. A change that breaks the contract goes through deprecation, because the API stays on `/api/v1/` ([0024](0024-one-api-on-api-v1.md)).

## Alternatives

- **The framework's default everywhere.** Rejected: some defaults break the contract (the envelope, the required body). Others drop a standard (RFC 8259, RFC 9110) or a safety property (the rollback, the 401).
