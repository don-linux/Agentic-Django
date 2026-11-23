# 0017. Error texts come from the code that raises the error

- Status: Accepted
- Date: 2026-11-23

## Context

The migration from DRF kept DRF's wording for every error, so that no assertion in `tests/http/` changed. Field errors were translated from Pydantic's ("This field is required.", "Not a valid string."), and the handlers hard-coded DRF's top-level details ("Authentication credentials were not provided."). The translation covered only some Pydantic error types, so one response could mix DRF and Pydantic texts under the same `code`. DRF parity stopped being a requirement on 2026-11-22.

## Decision

An error's text is the one its source gives:

- **Field errors.** A field error from request validation keeps Pydantic's error type as `code` and its message as `detail`. A key that is not a field keeps the registered code `unknown_field` ([0006](0006-one-error-body.md)).
- **Ninja.** Where Ninja raises the error, `detail` is its message: `Unauthorized`, `Forbidden`, `CSRF check Failed`, `Too many requests.` and `Cannot parse request body`. A 404 says `Not Found`, like Ninja's default handler, and a 405 says `Method not allowed`, like its router.
- **Django.** Outside Ninja, the error views use the titles of Django's default error pages: `Bad Request`, `Forbidden`, `Not Found` and `Server Error`.
- **The template's own.** It writes its own text only where neither framework has one: the 406, the 415, `validation_error` ("Invalid input."), `protected`, and the NUL check on a string.

The body, `{code, detail}` plus `fields` or `extra`, and every registered code stay as ADR 0006 says. See [api.md#error-body](../api.md#error-body).

## Consequences

- Clients keep branching on `code`. `detail` follows Pydantic, Ninja and Django and is not part of the contract, as [api.md](../api.md#error-body) already says.
- `core.api.exceptions` maps an exception to a code and reads the text from it. No translation table has to follow Pydantic's releases.
- A 404 never repeats the exception's message, like Ninja's default handler outside `DEBUG`. For example, `get_object_or_404` would name the model.
- `Retry-After` is still the wait rounded up, as Ninja sets it in `ninja/operation.py`.

## Alternatives

- **Keep DRF's texts as the template's own.** Rejected: they needed a translation layer and were mixed with Pydantic's.
- **Use Ninja's texts only where Ninja raises the error, and DRF's elsewhere.** Rejected: a 404 from a view and one from an unknown route would read differently.
- **Rename `unknown_field` to Pydantic's `extra_forbidden`.** Rejected: it is a registered code (ADR 0006, AGENTS.md).
