# 0022. Request fields validate strictly

- Status: Accepted
- Date: 2026-11-23

## Context

[0018](0018-the-api-accepts-exactly-what-its-contract-declares.md) made the API accept exactly what its contract declares, and the conformance test runs every schemathesis check with no exceptions. The endpoints the template ships pass. An audit added operations to a copy of the project with the field types the recipe used, then ran the conformance test with the same settings as CI. Every field type except text failed:

- In a request body, `int` took `false`, `bool` took `0`, `float` took `false`, and `UTCDateTime` and `date` took `0`. schemathesis reports each as a schema-violating request accepted (ST201).
- In a query parameter, `UTCDateTime` took `0.5` and `date` took `0`.

Ninja validates a request with Pydantic's `model_validate` on the parsed JSON, which is Pydantic's lax Python mode. Lax mode converts between JSON types: a string or a boolean for an integer, a number for a boolean, a Unix time (a number or a numeric string) for a datetime, a date alone or a value without a time zone for a datetime. `UUID` takes the form without hyphens, in braces and as a URN. The schema publishes `integer`, `boolean`, `date-time` (RFC 3339, which requires the time zone) and `uuid`.

`ConfigDict(strict=True)` does not fit Ninja: in Python mode, strict refuses the string that every datetime, date and UUID arrives as. Even in JSON mode, strict datetimes take a Unix time.

## Decision

- **JSON-native types in a body are Pydantic's strict types:** `StrictInt`, `StrictFloat` (which takes an integer, as JSON Schema's `number` does) and `StrictBool`.
- **A query parameter keeps the lax type,** because its value is text: `int`, `bool`, and `FiniteFloat`, which refuses `nan` and `inf`.
- **Datetimes are exact RFC 3339.** `core.api.schemas.UTCDateTime` takes the date, `T`, the time with seconds, and a time zone (`Z` or `±hh:mm`). Anything else is a 400 with Pydantic's error types (`datetime_type`, `datetime_parsing`): a number, a date alone, a space for `T`, or no time zone. The year is 1000 to 9998, which the request schema publishes as a `pattern`. Python's `datetime` ends at years 1 and 9999, so an offset would push `0001-01-01T00:00:00+00:01` past it in UTC: schemathesis found that 500 in the audit's final run. Output is unchanged: UTC with `Z`, microseconds kept, and its schema has no `pattern`.
- **Dates are exact too.** `core.api.schemas.ISODate` takes a full-date only.
- **UUIDs a request sends** use `core.api.schemas.PublicId`: the hyphenated form, in either case, which the path converter already requires.
- `docs/api.md#field-types` lists the types for a body, a query parameter and a response.

## Consequences

- A value without a time zone was read as UTC ([0021](0021-what-stays-after-drf-parity-and-why.md)). It is now a 400, so the server never guesses a zone.
- No endpoint the template ships has an integer, number, boolean, date, datetime or UUID in a request, so the contract and `tests/http/` do not change.
- A new field with a lax type fails the conformance test with ST201. The fix is the type in the table, never an exception in the test.

## Alternatives

- **Keep the lax types and declare what they take**, for example `anyOf[integer, string]`. Rejected: generated clients would get a vaguer contract, and RFC 3339 has no form for a Unix time.
- **Keep reading a value without a time zone as UTC.** Rejected by Fernando on 2026-11-23: it accepts more than `date-time` declares, and the client knows its zone better than the server.
- **Global strict mode,** with lax fields opted back in. Rejected: Ninja validates in Python mode, where it breaks every datetime, date and UUID.
