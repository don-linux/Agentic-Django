# Decisions

Short records of why the template is the way it is, one decision per file (a light [MADR](https://adr.github.io/madr/)). They exist so that a person or an agent who comes back months later does not undo a choice by accident. The rules themselves live in [AGENTS.md](../../AGENTS.md) and the area docs; a record links to them and keeps only the reasons and the rejected alternatives.

| # | Decision | Status |
| --- | --- | --- |
| [0001](0001-record-decisions.md) | Record decisions in `docs/decisions/` | Accepted |
| [0002](0002-accounts-through-allauth-headless.md) | Accounts go through django-allauth headless | Accepted |
| [0003](0003-public-uuidv7-ids.md) | The API exposes a UUIDv7 `public_id`, never the integer key | Accepted |
| [0004](0004-users-are-deactivated-not-deleted.md) | Users are deactivated or anonymized, not deleted | Accepted |
| [0005](0005-business-logic-in-services-and-selectors.md) | Business logic lives in services and selectors; DRF stays in `api/` | Superseded by 0013 |
| [0006](0006-one-error-body.md) | One error body, `{"code", "detail"}`, not RFC 9457 | Accepted |
| [0007](0007-http-and-drf-test-layout.md) | Tests split into `http/` and `drf/` | Superseded by 0012 |
| [0008](0008-dependency-audit-is-required.md) | The dependency audit is a required check | Accepted |
| [0009](0009-bruno-runtime-variables.md) | Bruno scripts store values with `bru.setVar` | Accepted |
| [0010](0010-versioned-api-and-breaking-changes.md) | The API is versioned under `/api/v1/`; a breaking change needs `/api/v2/` | Superseded by 0011 |
| [0011](0011-openapi-contract-as-native-json.md) | The API contract is versioned as native OpenAPI 3.1.0 JSON | Accepted |
| [0012](0012-http-and-ninja-test-layout.md) | Tests split into `http/` and `ninja/` | Accepted |
| [0013](0013-business-logic-in-services-and-selectors.md) | Business logic lives in services and selectors; the API framework stays in `api/` | Accepted |
| [0014](0014-user-extends-abstractbaseuser-and-permissionsmixin.md) | `User` extends `AbstractBaseUser` and `PermissionsMixin`, not `AbstractUser` | Accepted |

## Writing one

- Copy [template.md](template.md) to `NNNN-short-title.md` with the next number, and add a row above.
- Write it in the pull request that makes the decision. Keep it under a page: context, decision, consequences, alternatives.
- Never rewrite an accepted record. A new decision that replaces one gets its own file; set the old one's status to `Superseded by NNNN` and change its row.
- Before changing something a record covers, read the record. If the reason no longer holds, supersede the record in the same pull request.
