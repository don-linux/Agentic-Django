# 0022. Request fields validate strictly

- Status: Accepted
- Date: 2026-11-23

## Context

Ninja validates a request with Pydantic's `model_validate` on the parsed JSON, which is Pydantic's lax Python mode. Lax mode converts between JSON types:

- a string or a boolean for an integer, an integer choice included;
- a number for a boolean;
- a JSON number for a decimal, read through a float, which rounds it;
- a Unix time (a number or a numeric string) for a datetime;
- a date alone, or a value without a time zone, for a datetime;
- for a UUID, the form without hyphens, in braces, or as a URN.

The schema publishes `integer`, `boolean`, `date-time` (RFC 3339, which requires the time zone) and `uuid`. So a lax field takes values the contract forbids, and the conformance test reports each one as a schema-violating request accepted (ST201). In a body, an `int` field takes `false`, a `bool` takes `0`, and a date or datetime takes `0`. In a query, a datetime takes `0.5`.

`ConfigDict(strict=True)` does not fit Ninja: in Python mode, strict refuses the string that every datetime, date and UUID arrives as. Even in JSON mode, strict datetimes take a Unix time.

## Decision

- **JSON-native types in a body take their own JSON type only:** `core.api.schemas.JsonInt` for an integer, which takes any number with a zero fractional part (`2.0`), as JSON Schema 2020-12's `integer` does, and Pydantic's `StrictFloat` (which takes an integer, as JSON Schema's `number` does) and `StrictBool`. Pydantic's `StrictInt` refuses `2.0`, which the schema allows.
- **A query parameter keeps the lax type,** because its value is text: `int`, `bool`, and `FiniteFloat`, which refuses `nan` and `inf`.
- **Datetimes are exact RFC 3339.** `core.api.schemas.UTCDateTime` takes the date, `T`, the time with seconds, and a time zone (`Z` or `±hh:mm`). Anything else is a 400 with Pydantic's error types (`datetime_type`, `datetime_parsing`): a number, a date alone, a space for `T`, or no time zone. The year is 1000 to 9998, which the request schema publishes as a `pattern`. Python's `datetime` ends at years 1 and 9999, so an offset would push `0001-01-01T00:00:00+00:01` past it in UTC, an `OverflowError`. Output is unchanged: UTC with `Z`, microseconds kept, and its schema has no `pattern`.
- **Dates are exact too.** `core.api.schemas.ISODate` takes a full-date only.
- **UUIDs a request sends** use `core.api.schemas.PublicId`: the hyphenated form, in either case, which the path converter already requires. A detail path takes `PublicIdPath`, which publishes that converter's pattern.
- **Integer choices** (`IntegerChoices`, `IntEnum`, an integer `Literal`) carry `core.api.schemas.JsonInteger` in a body: only a JSON integer reaches the enum.
- **Decimals are strings.** `core.api.schemas.DecimalString` takes the model field's `max_digits` and `decimal_places` and publishes the exact `pattern`, so `full_clean()` never refuses a value the schema allows. A JSON number is a 400, and a response writes the decimal in positional notation.
- **Free text in a query** goes through `storable_text`, as a body does: `QueryText` for a parameter, `BaseFilterSchema` for a list's filters.
- `docs/api.md#field-types` lists the types for a body, a query parameter and a response.

## Consequences

- A value without a time zone is a 400: the server never guesses a zone.
- No endpoint the template ships has an integer, number, boolean, decimal, choice, date, datetime or UUID in a request body, so the contract and `tests/http/` do not change. `core/tests/ninja/test_representation.py` sends each type the values lax mode would take.
- A new field with a lax type fails the conformance test with ST201. The fix is the type in the table, never an exception in the test.

## Alternatives

- **Keep the lax types and declare what they take**, for example `anyOf[integer, string]`. Rejected: generated clients would get a vaguer contract, and RFC 3339 has no form for a Unix time.
- **Read a value without a time zone as UTC.** Rejected: it accepts more than `date-time` declares, and the client knows its zone better than the server.
- **Global strict mode,** with lax fields opted back in. Rejected: Ninja validates in Python mode, where it breaks every datetime, date and UUID.
