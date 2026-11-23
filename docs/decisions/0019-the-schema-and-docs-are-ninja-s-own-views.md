# 0019. The schema and docs are Ninja's own views

- Status: Accepted
- Date: 2026-11-23

## Context

`/api/v1/schema/` and `/api/v1/docs/` were project views that reproduced drf-spectacular:

- YAML by default (`application/vnd.oai.openapi`), and four media types chosen by `Accept`;
- JSON indented by an `indent` parameter, with U+2028 escaped;
- an OPTIONS answer in the schema's format;
- a Swagger page whose init script came from `?script=`.

They kept the URL names and the HTTP contract of the DRF version. DRF parity stopped being a requirement on 2026-11-22, and the committed contract has been JSON since [0011](0011-openapi-contract-as-native-json.md).

Ninja 1.8.0 serves both itself: `NinjaAPI(openapi_url=..., docs_url=..., docs_decorator=...)`. Its Swagger template keeps the settings in a `<script type="application/json">` data block and loads its init code from the static file `ninja/swagger-ui-init.js` (through `{% static %}`, so production serves it under its hashed name), so it needs no inline script under the CSP.

## Decision

- **Ninja serves both.** `core.api.api` sets `openapi_url="/schema/"`, `docs_url="/docs/"` and `docs_decorator=staff_docs_required`, so the paths are unchanged.
- **The schema is JSON only.** It is the same document as `docs/openapi.json`; any other `Accept` is 406.
- **The docs are Ninja's Swagger page.**
- **The staff gate stays.** `staff_docs_required` (`core/api/auth.py`) answers the API's JSON 401 and 403 and accepts the `X-Session-Token`. The guide's `staff_member_required` would redirect to the admin login.
- **New URL names.** The views are Ninja's `api:openapi-json` and `api:openapi-view`.
- **PyYAML is a development dependency.** At run time nothing imports it now; only tests read YAML.

See [api.md#representation](../api.md#representation) and [security.md](../security.md#content-security-policy).

## Consequences

- A client that read the YAML schema or sent `; indent=N` now gets JSON or a 406. The schema route is not part of the contract, so oasdiff sees no change.
- The project no longer renders or negotiates the schema itself.
- `tests/http/` checks the schema and docs by their literal paths, and the docs page by its static init script.

## Alternatives

- **Keep the YAML views.** Rejected: they reproduce another framework's renderers with no reason of their own.
- **`staff_member_required` as the decorator.** Rejected: an API client would get an HTML redirect instead of a JSON 401 or 403.
