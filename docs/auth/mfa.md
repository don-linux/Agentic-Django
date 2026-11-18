# Two-factor authentication

`allauth.mfa` is installed with `MFA_SUPPORTED_TYPES = []`, so allauth mounts only `auth/2fa/authenticate`, `auth/2fa/reauthenticate` and `account/authenticators` (which lists nothing). To offer TOTP and recovery codes:

1. In `config/settings/base.py`, set `MFA_SUPPORTED_TYPES = ["totp", "recovery_codes"]`. allauth then mounts `/_allauth/app/v1/account/authenticators/totp` and `.../recovery-codes`. `MFA_TOTP_ISSUER` is already the project's display name (`DJANGO_EMAIL_SIGNATURE_NAME`), the label authenticator apps show.
2. Replace the inert-MFA tests in `apps/users/tests/http/test_mfa.py` with tests for setup (`GET` and `POST` on `account/authenticators/totp`), a login that stops at the `mfa_authenticate` flow and finishes with `auth/2fa/authenticate`, recovery codes, and deactivation.
3. Add `templates/mfa/email/` overrides for the notifications MFA sends (`totp_activated`, `totp_deactivated`, `recovery_codes_generated`) before turning it on ([../email.md#templates](../email.md#templates)).
4. Keep the server clock synchronized: TOTP codes depend on it ([../NEXT-STEPS.md#server-clock](../NEXT-STEPS.md#server-clock)).

The Django admin login keeps its password and lockout ([../security.md#admin-lockout](../security.md#admin-lockout)); this recipe adds MFA to the API, not to `/admin/`.
