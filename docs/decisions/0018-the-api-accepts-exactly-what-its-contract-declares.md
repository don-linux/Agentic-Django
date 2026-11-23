# 0018. The API accepts exactly what its contract declares

- Status: Accepted
- Date: 2026-11-23
- Supersedes: [0016](0016-api-checked-against-openapi-with-schemathesis.md)

## Context

[0016](0016-api-checked-against-openapi-with-schemathesis.md) added the schemathesis conformance test, with a `filter_failure` hook for the input the API accepted on purpose although its schema forbids it: a `page_size` that is not an integer, a number for `name`, and `id` or `url` in a user `PATCH`. Those leniencies kept DRF's behavior so that `tests/http/` would not change. DRF parity stopped being a requirement on 2026-11-22.

With the filter gone, schemathesis and a reading of the code found more: an empty `page` or `page=last`, a malformed `page` answered with 404, a `name` over `maxLength` accepted when padded with spaces (`str_strip_whitespace` trims before the length check), and a 400 on the list that the schema did not declare.

## Decision

The server accepts exactly what `docs/openapi.json` declares, and the schema declares everything the server requires:

- **List parameters.** `page` and `page_size` are whole numbers of at least 1, as in Ninja's `PageNumberPagination`. Anything else is a 400 `validation_error`, which list operations now declare. A `page_size` above 100 is cut to 100, as Ninja does, and a page past the last one is a 404.
- **Request bodies.** A number for a string is a 400, a read-only key such as `id` is `unknown_field` like any other key, and strings are stored as sent, so `maxLength` counts every character.
- **Validation order.** Ninja validates the path, the query and the body before the view looks anything up, so an invalid request is a 400 whether or not the object exists.
- **The conformance test.** `core/tests/test_openapi_conformance.py` runs every schemathesis check with no `filter_failure`.

One generation setting stays: `generation.allow-x00` is off. PostgreSQL text cannot store NUL, so the API rejects `\x00` in a string. schemathesis documents this setting for servers and drivers that reject NUL bytes. Declaring the rule as a `pattern` would be a breaking change in the `api-contract` job (oasdiff `request-property-pattern-added`).

See [api.md#contract-conformance](../api.md#contract-conformance).

## Consequences

- A conformance failure is a server bug or a schema bug. There is no exception list to grow.
- A client that relied on a leniency gets a 400. This changed `tests/http/` assertions on purpose, in the same pull request.
- `minimum: 1` on `page` and `page_size` sits inside `anyOf[integer, null]`, which oasdiff 1.39.0 does not report. The parameters stay nullable, because removing `null` is an oasdiff ERR.
- NUL handling is still tested directly (`null_characters_not_allowed`), just not generated.

## Alternatives

- **Keep the filter for the leniencies.** Rejected: the contract would keep lying.
- **Describe the leniencies in the schema**, for example `name` as a string or a number. Rejected: generated clients would get a vaguer contract.
- **A `pattern` that forbids NUL.** Rejected: it is a breaking change for oasdiff, and the server already refuses NUL.
