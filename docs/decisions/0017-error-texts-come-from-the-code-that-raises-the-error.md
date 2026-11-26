# 0017. Error texts come from the code that raises the error

- Status: Accepted
- Date: 2026-11-23

## Context

An API error body carries a `code` and a `detail` ([0006](0006-one-error-body.md)). Its text can come from three places:

- Pydantic, for a field error in request validation;
- Ninja, for the errors it raises (authentication, CSRF, throttling, a body it cannot parse, a method the router lacks);
- Django, for a request that never reaches an API view.

A house style for every text needs a translation table that follows each Pydantic and Ninja release. Every error type the table misses leaks the original text, so one response can mix two styles under the same `code`.

## Decision

An error's text is the one its source gives:

- **Field errors.** A field error from request validation keeps Pydantic's error type as `code` and its message as `detail`. A key that is not a field keeps the registered code `unknown_field` ([0006](0006-one-error-body.md)).
- **Ninja.** Where Ninja raises the error, `detail` is its message: `Unauthorized`, `Forbidden`, `CSRF check Failed`, `Too many requests.` and `Cannot parse request body`. A 404 says `Not Found`, like Ninja's default handler, and a 405 says `Method not allowed`, like its router.
- **Django.** Outside Ninja, the error views use the titles of Django's default error pages: `Bad Request`, `Forbidden`, `Not Found` and `Server Error`. A NUL in a string keeps the code and message of Django's `ProhibitNullCharactersValidator`.
- **The template's own.** It writes its own text only where neither framework has one: the 406, the 415, `validation_error` ("Invalid input.") and `protected`.

The body, `{code, detail}` plus `fields` or `extra`, and every registered code follow ADR 0006. See [api.md#error-body](../api.md#error-body).

## Consequences

- Clients branch on `code`. `detail` follows Pydantic, Ninja and Django, and is not part of the contract ([api.md](../api.md#error-body)).
- `core.api.exceptions` maps an exception to a code and reads the text from it. No translation table has to follow Pydantic's releases.
- A 404 never repeats the exception's message, like Ninja's default handler outside `DEBUG`: `get_object_or_404` would name the model.
- `Retry-After` is the wait rounded up, as Ninja sets it in `ninja/operation.py`.

## Alternatives

- **A house style for every text.** Rejected: it needs a translation layer that follows Pydantic and Ninja, and it mixes two styles wherever the layer misses a type.
- **Ninja's texts where Ninja raises the error, and the template's elsewhere.** Rejected: a 404 from a view and one from an unknown route would read differently.
- **Rename `unknown_field` to Pydantic's `extra_forbidden`.** Rejected: it is a registered code (ADR 0006, AGENTS.md).
