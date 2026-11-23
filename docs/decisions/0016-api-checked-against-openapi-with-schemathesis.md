# 0016. The API is checked against its OpenAPI contract with schemathesis

- Status: Superseded by 0018
- Date: 2026-11-22

## Context

`docs/openapi.json` is the API contract ([0011](0011-openapi-contract-as-native-json.md)). Two jobs already guard it, and neither sends a request:

- `lint` proves the file matches the code that generates it.
- `api-contract` runs oasdiff, which rejects a breaking change to it.

Nothing checked that the server answers what the schema says, and `tests/http/` pins only hand-written cases. A probe with schemathesis found a contract bug none of them saw: every `400 validation_error` body matched both branches of its `oneOf`, because `ErrorSchema` allowed additional properties.

The Ninja migration left schemathesis for a later pull request.

## Decision

`core/tests/test_openapi_conformance.py` runs schemathesis on every operation of the schema the API publishes. Requests go through the WSGI application in the test process, and all of schemathesis's default checks run. The test runs with the suite and is deterministic.

The API accepts some input on purpose although its schema calls it invalid. `filter_failure` drops those failures, with one predicate per behavior, and each behavior has a test of its own that pins it. The rules are in [docs/api.md#contract-conformance](../api.md#contract-conformance).

## Consequences

- A new endpoint is checked against its schema without a new test. Any of these fails the suite: a wrong status code, body or content type, a missing 401, or a 5xx.
- The test commits to the database (`transactional_db`), because the WSGI handler closes the connection of a test transaction.
- There are two new dev dependencies: schemathesis and Hypothesis.
- Every run sends the same requests. An upgrade of Hypothesis or schemathesis may generate new ones and find something new; treat that as a bug report.
- Do not widen `filter_failure` to make a failure pass. Accepting a new kind of input takes a decision and a test that pins the behavior.

## Alternatives

- **The schemathesis CLI against a running server, as in the Bruno job.** It gives no coverage of the code, the development throttles (60/min) would answer 429, and a failure is harder to reproduce.
- **Excluding a check or an operation.** The check would be lost for every other input too.
- **Making the server reject the DRF-parity input.** That changes behavior that `tests/http/` pins, so it is a separate decision.
- **Describing the lenient input in the schema**, for example `name` as a string or a number. Generated clients would get a less precise contract.
