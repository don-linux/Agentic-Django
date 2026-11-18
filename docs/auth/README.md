# Optional authentication features

django-allauth can do more than the template turns on. Accounts sign up and log in with an email and a password, verified by a code; everything below is off by default, and each page is a short recipe to turn one feature on. Why the template has no `django.contrib.sites` and keeps registration open is in [ARCHITECTURE.md#authentication](../ARCHITECTURE.md#authentication).

- [mfa.md](mfa.md): two-factor authentication (TOTP and recovery codes). `allauth.mfa` is installed but inert.
- [phone.md](phone.md): phone numbers and SMS codes. allauth's phone routes are mounted but refuse every number.
- [oauth.md](oauth.md): login with a third-party provider. `allauth.socialaccount` is not installed.

Whichever you turn on:

1. Add the tests: every new route for an anonymous and a logged-in client (no 500), the happy path, and the error codes your client branches on.
2. Add the account emails the feature sends before it goes live ([../email.md#templates](../email.md#templates)).
3. Update the inert-route notes in [../api.md](../api.md#out-of-scope-_allauth) and the AGENTS.md invariants if the feature changes them.
