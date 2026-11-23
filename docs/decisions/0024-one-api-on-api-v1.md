# 0024. One API, on /api/v1/, with its contract as native OpenAPI 3.1.0 JSON

- Status: Accepted
- Date: 2026-11-23
- Supersedes: [0011](0011-openapi-contract-as-native-json.md)

## Context

This repository is a template: a project starts from it, keeps its code and conventions, and builds its own endpoints on them. [0011](0011-openapi-contract-as-native-json.md) made `docs/openapi.json` the committed contract. It also said that an incompatible change goes to `/api/v2/`, but nothing in the template supports a second version: `ApiContractMiddleware` serves `/api/v1/` only, the conformance test reads one schema, and the endpoint skill forbids a second `NinjaAPI`.

## Decision

- **One API.** Every route is under `/api/v1/`, served by the one `NinjaAPI` in `config/api.py`. There is no second version and no second API instance.
- **The contract.** `docs/openapi.json` is the committed contract, OpenAPI 3.1.0 as Ninja generates it. CI fails when the file drifts from the code. On a pull request, oasdiff fails on a breaking change against the base branch.
- **Changes are compatible.** A breaking change is not made. The replacement is added next to the old endpoint or field, and the old one is deprecated, announced with `Deprecation` and `Sunset`, and answers 410 `gone` after the sunset date ([api.md#deprecating-endpoints-and-fields](../api.md#deprecating-endpoints-and-fields)).

See [api.md#breaking-changes](../api.md#breaking-changes).

## Consequences

- Regenerate the contract with `python manage.py export_openapi_schema --settings config.settings.test --api config.api.api --indent 2 > docs/openapi.json`. The schema goes to stdout, and the redirect ends the file with the one newline pre-commit expects. `--output` writes no final newline, so it is not used.
- `--api config.api.api` names the instance that has every router mounted once its module is imported. `core/tests/ninja/test_openapi_export.py` checks that instance in a process that never imports the URLconf.
- The `api-contract` job runs oasdiff with `--flatten-allof`, because Ninja wraps an optional body in a one-item `allOf`. Error responses are declared per operation, so they are part of the contract.
- A client never has to move to another version. A deprecated endpoint stays in the contract, answering 410 after its sunset, until the project decides to drop it.

## Alternatives

- **A second version, `/api/v2/`, for incompatible changes.** Rejected: a project built from the template starts on `/api/v1/` and stays there, and clients never have to move.
- **Support several versions in the template**: a middleware and a conformance test per API, and an exception in the skill. Rejected: it is code for a need the template does not have.
