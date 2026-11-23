# 0021. What stays after DRF parity, and why

- Status: Accepted
- Date: 2026-11-23

## Context

DRF parity stopped being a requirement on 2026-11-22. Most of what the migration copied from DRF went:

- the input leniencies ([0018](0018-the-api-accepts-exactly-what-its-contract-declares.md));
- the error wording ([0017](0017-error-texts-come-from-the-code-that-raises-the-error.md));
- the schema views ([0019](0019-the-schema-and-docs-are-ninja-s-own-views.md));
- OPTIONS, `Allow` and the 406 order ([0020](0020-options-allow-and-406-follow-django-and-rfc-9110.md)).

Some behaviors that look like DRF stay, because they have a reason of their own. Without a record, a later agent could remove them as leftovers.

## Decision

These stay, each for the reason given:

- **Pagination envelope.** It stays `count`, `next`, `previous`, `results`, with absolute links, 20 per page and at most 100.
  - Ninja's native `{items, count}` is a breaking change: oasdiff reports 3 ERR (`response-required-property-removed`).
  - A page past the last one, or deeper than `DJANGO_API_MAX_PAGE_NUMBER`, is a 404, because every page costs a `COUNT(*)` and an `OFFSET`.
- **Optional `PATCH` body.** An empty `PATCH` changes nothing, as `{}` does. A required body is an oasdiff ERR (`request-body-became-required`), so the one-item `allOf` and `--flatten-allof` stay.
- **`UTCDateTime`.** Output is UTC with `Z`. Microseconds are kept, so a client can send a timestamp back exactly, for example in a filter; Ninja's encoder would cut them to milliseconds. Input is exact RFC 3339 with a time zone ([0022](0022-request-fields-validate-strictly.md)); this record first read a value without one as UTC.
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

- These are the template's own behaviors now. Their comments and docs give these reasons, not DRF.
- Changing one is a contract decision with its own record, and some of them need `/api/v2/`.

## Alternatives

- **Remove everything DRF had, whatever its reason.** Rejected: some removals break the contract, and others drop a standard (RFC 8259, RFC 9110) or a safety property (the rollback, the 401).
