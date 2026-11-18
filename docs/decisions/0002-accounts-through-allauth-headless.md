# 0002. Accounts go through django-allauth headless

- Status: Accepted
- Date: 2026-11-18

## Context

Signup, login, email verification, password reset, sessions and MFA are security-sensitive and easy to get subtly wrong. The template serves only a JSON API to an app.

## Decision

django-allauth in headless mode (`HEADLESS_ONLY = True`) owns every account flow under `/_allauth/`, with the session in the `X-Session-Token` header. The template customizes it through `apps/users/adapters.py`, not by rewriting its views. See [ARCHITECTURE.md#authentication](../ARCHITECTURE.md#authentication) and [auth/](../auth/README.md).

## Consequences

- No service, serializer or view re-implements an account flow ([ARCHITECTURE.md#services-and-selectors](../ARCHITECTURE.md#services-and-selectors)).
- `/_allauth/` keeps allauth's own response format and conventions, which differ from `/api/v1/` ([api.md#out-of-scope-_allauth](../api.md#out-of-scope-_allauth)).
- allauth security releases are applied at once (AGENTS.md).
- allauth is independent of the API framework: a move away from DRF keeps it.

## Alternatives

- dj-rest-auth or hand-written views on DRF: rejected, more code to keep secure, and tied to DRF.
