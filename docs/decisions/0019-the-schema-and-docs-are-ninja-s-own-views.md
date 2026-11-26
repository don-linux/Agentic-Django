# 0019. The schema and docs are Ninja's own views

- Status: Accepted
- Date: 2026-11-23

## Context

The API publishes its OpenAPI document and a Swagger page for staff. Ninja 1.8.0 serves both through `NinjaAPI(openapi_url=..., docs_url=..., docs_decorator=...)`.

Its Swagger template keeps the settings in a `<script type="application/json">` data block. It loads its init code from the static file `ninja/swagger-ui-init.js`, through `{% static %}`, so production serves it under its hashed name. It therefore needs no inline script under the CSP. The committed contract is JSON ([0024](0024-one-api-on-api-v1.md)).

## Decision

- **Ninja serves both.** `core.api.api` sets `openapi_url="/schema/"`, `docs_url="/docs/"` and `docs_decorator=staff_docs_required`.
- **The schema is JSON only.** It is the same document as `docs/openapi.json`, and any other `Accept` is 406.
- **The docs are Ninja's Swagger page.**
- **A staff gate.** `staff_docs_required` (`core/api/auth.py`) answers the API's JSON 401 and 403, and accepts the `X-Session-Token`. The guide's `staff_member_required` would redirect to the admin login.
- **URL names.** The views are Ninja's `api:openapi-json` and `api:openapi-view`. `core.api.negotiation` recognizes them by those names.
- **No YAML at run time.** PyYAML is a development dependency, for the tests that read YAML.

See [api.md#representation](../api.md#representation) and [security.md](../security.md#content-security-policy).

## Consequences

- The schema route is not part of the contract, and oasdiff does not compare it.
- The template neither renders nor negotiates the schema itself.
- `tests/http/` checks the schema and docs by their literal paths, and the docs page by its static init script.

## Alternatives

- **Project views that render YAML and several media types.** Rejected: more code, for formats no client needs.
- **`staff_member_required` as the decorator.** Rejected: an API client would get an HTML redirect instead of a JSON 401 or 403.
