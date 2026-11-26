# 0018. The API accepts exactly what its contract declares

- Status: Accepted
- Date: 2026-11-23
- Supersedes: [0016](0016-api-checked-against-openapi-with-schemathesis.md)

## Context

`docs/openapi.json` is the contract that clients generate code from. When the server accepts input the schema forbids, a client written against the schema and one written against the server disagree. The schemathesis conformance test then needs an exception list, which grows with every such leniency. Typical ones are:

- a `page_size` that is not an integer, silently replaced by the default;
- a number accepted for a string;
- a read-only or unknown key ignored in a body;
- whitespace trimmed before `maxLength` is checked;
- a malformed `page` answered with 404;
- a 400 that an operation does not declare.

## Decision

The server accepts exactly what `docs/openapi.json` declares, and the schema declares everything the server requires:

- **List parameters.** `page` and `page_size` are whole numbers of at least 1, as in Ninja's `PageNumberPagination`. Anything else is a 400 `validation_error`, which list operations declare. A `page_size` above 100 is cut to 100, as Ninja does, and a page past the last one is a 404.
- **Request bodies.** A value of another JSON type is a 400 ([0022](0022-request-fields-validate-strictly.md)). A read-only key such as `id` is `unknown_field` like any other key. Strings are stored as sent, so `maxLength` counts every character.
- **Validation order.** Ninja validates the path, the query and the body before the view looks anything up, so an invalid request is a 400 whether or not the object exists.
- **The conformance test.** `core/tests/test_openapi_conformance.py` runs every schemathesis check with no `filter_failure`. Its generation settings are:
  - `allow-x00` off. PostgreSQL text cannot store NUL, so every input schema refuses `\x00` with `null_characters_not_allowed`. schemathesis documents this setting for servers and drivers that reject NUL bytes, and a `pattern` in the schema would be a breaking change (oasdiff `request-property-pattern-added`).
  - `with-security-parameters` off. The test sends a real session token.
  - A dictionary for path ids, so that half of the requests to a detail route use the caller's own id.

See [api.md#contract-conformance](../api.md#contract-conformance).

## Consequences

- A conformance failure is a server bug or a schema bug. There is no exception list to grow.
- `minimum: 1` on `page` and `page_size` sits inside `anyOf[integer, null]`, which oasdiff 1.39.0 does not report. The parameters stay nullable, because removing `null` is an oasdiff ERR.
- NUL and lone surrogates are tested directly (`core/tests/ninja/test_storable_text.py`), because schemathesis generates neither.

## Alternatives

- **An exception list in the conformance test.** Rejected: the contract would not describe the server.
- **Describe the leniencies in the schema**, for example `name` as a string or a number. Rejected: generated clients would get a vaguer contract.
- **A `pattern` that forbids NUL.** Rejected: it is a breaking change for oasdiff, and the server already refuses NUL.
