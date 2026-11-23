# Email in production

Verification is mandatory, so without working email nobody can finish signing up. Production uses Django's SMTP backend through `MAILERS` and the `DJANGO_MAILER_*` variables, which works with any provider, and it refuses to start with example values (`config/settings/guards.py`).

## How it works

- allauth sends verification and password-reset codes synchronously, inside the request. That is on purpose: the code never sits in the Celery broker. To send email through Celery instead, see [Optional: sending email through Celery](#optional-sending-email-through-celery).
- The request waits up to `DJANGO_MAILER_TIMEOUT` seconds (default 10) for each SMTP step.
- When SMTP fails, `apps.users.adapters.AccountAdapter` logs the error and the request still returns the normal pending flow. The client can ask for a new code at `POST /_allauth/app/v1/auth/email/verify/resend`, up to two times per flow; logging in again starts a new flow.
- Error reports go to `DJANGO_ADMINS` (nobody by default, and nobody while `SENTRY_DSN` is set, since Sentry already reports errors) through the `admin-logging` mailer, which drops the message instead of failing when SMTP is down.
- Development sends to Mailpit (<http://localhost:8025>), and tests use the in-memory backend.

- Only SMTP and OS transport errors are swallowed; a template error still fails the request. The log line names the template and the error class, with no traceback: never log an email body (it holds the verification or reset code) or the recipient (`SMTPRecipientsRefused` carries it).
- Production requires `DJANGO_MAILER_HOST`, because without SMTP nobody can sign up. Error emails must never raise while Django is already handling a 500, and with `MAILERS` defined `AdminEmailHandler` no longer passes `fail_silently` on its own, so the `admin-logging` mailer sets it.
- `DJANGO_ADMINS` is a comma-separated list of addresses, each optionally `"Name" <email>` (no commas in names). In `docker/prod/.env`, quote the whole value for Compose.
- Every account email shares `DJANGO_DEFAULT_FROM_EMAIL` and the subject prefix `DJANGO_EMAIL_SUBJECT_PREFIX`. allauth's prefix must be set, because without `django.contrib.sites` it has no site name to fall back to.
- The context keys `email_signature_name` and `frontend_url` are defaults for `base_message.txt`; a value allauth already put in the context wins.

- Production refuses to start until `DJANGO_MAILER_HOST`, `DJANGO_DEFAULT_FROM_EMAIL` and `FRONTEND_URL` are real: no `example.com` or `localhost` in From and `FRONTEND_URL`, and not TLS and SSL at once. The SMTP variables are `DJANGO_MAILER_HOST`, `DJANGO_MAILER_PORT`, `DJANGO_MAILER_USERNAME`, `DJANGO_MAILER_PASSWORD`, `DJANGO_MAILER_USE_TLS` and `DJANGO_MAILER_USE_SSL`, one per option of Django's `MAILERS` setting. Gmail only suits a prototype.

## Templates

allauth renders plain-text emails only; the API renders no HTML. The overrides live in `templates/account/email/`, and each message extends `base_message.txt`. Its signature starts with the RFC 3676 delimiter, two dashes and a space. The trailing space is part of it, so the template keeps a comment right after it that stops editors and hooks from stripping the space. The signature name comes from `DJANGO_EMAIL_SIGNATURE_NAME`.

Every account email the template sends, and its files:

| Email | Sent when | Templates |
| --- | --- | --- |
| Verification code after signup | Signup, a resend, a login while the address is unverified | `email_confirmation_signup_message.txt`, subject `email_confirmation_subject.txt` |
| Verification code for an added address | `POST /_allauth/app/v1/account/email` | `email_confirmation_message.txt`, subject `email_confirmation_subject.txt` |
| Password reset code | A reset request for an address with an account | `password_reset_code_message.txt`, `password_reset_code_subject.txt` |
| Account already exists | A signup with an address that has an account (enumeration prevention) | `account_already_exists_message.txt`, `account_already_exists_subject.txt` |
| No account | A reset request for an address without an account (enumeration prevention) | `unknown_account_message.txt`, `unknown_account_subject.txt` |

Flows the template leaves off fall back to allauth's stock English templates, which talk about a browser and a site. Add your own `templates/account/email/` files before turning one on:

- Account notifications (`ACCOUNT_EMAIL_NOTIFICATIONS`): `email_changed`, `email_confirm`, `email_deleted`, `password_changed`, `password_reset` and `password_set`, plus `base_notification.txt`.
- Login by code (`ACCOUNT_LOGIN_BY_CODE_ENABLED`), and reauthentication by code if `ACCOUNT_REAUTHENTICATION_METHODS` adds `"code"`: `login_code`.
- MFA ([auth/mfa.md](auth/mfa.md)): its notifications live in `templates/mfa/email/` (`totp_activated`, `totp_deactivated`, `recovery_codes_generated`, `webauthn_added`, `webauthn_removed`).

Each needs a `_subject.txt` and a `_message.txt` that extends `account/email/base_message.txt`.

## Choosing a provider

Any transactional provider with SMTP works without code changes. Typical values (confirm them in the provider's docs, they change):

| Provider | `DJANGO_MAILER_HOST` | Port | Username / password |
| --- | --- | --- | --- |
| Amazon SES | `email-smtp.<region>.amazonaws.com` | 587 | SMTP credentials created in the SES console (not your IAM keys) |
| Postmark | `smtp.postmarkapp.com` | 587 | The server API token as both username and password |
| Resend | `smtp.resend.com` | 587 | Username `resend`, password an API key |
| Brevo | `smtp-relay.brevo.com` | 587 | Your SMTP login and an SMTP key |
| Mailgun | `smtp.mailgun.org` | 587 | The domain's SMTP user and password |

- Port 587 uses STARTTLS: `DJANGO_MAILER_USE_TLS=True`, `DJANGO_MAILER_USE_SSL=False`.
- Port 465 uses implicit TLS: `DJANGO_MAILER_USE_SSL=True`, `DJANGO_MAILER_USE_TLS=False`. Never both; production refuses that.
- Gmail is fine for a prototype only: it needs an app password (<https://myaccount.google.com/apppasswords>), has a low daily limit, and rewrites any From that is not the account or one of its aliases.

## DNS for deliverability

- Send from an address on your own domain (`DJANGO_DEFAULT_FROM_EMAIL`).
- Verify the domain with the provider, then publish the SPF include and the DKIM records it gives you.
- Add DMARC, starting with `v=DMARC1; p=none; rua=mailto:dmarc@<domain>` and tightening to `quarantine` and then `reject` once the reports are clean.
- If the provider supports a custom return-path, use a subdomain such as `mail.<domain>`.

Without SPF and DKIM aligned with the From domain, Gmail and Outlook send verification codes to spam or reject them.

## VPS notes

- Do not run your own mail server.
- Many VPS providers block outbound port 25, and some block 465 and 587 on new accounts until you ask support.
- Test from the server:

  ```bash
  docker compose -f docker/prod/compose.yaml exec django python manage.py sendtestemail you@your-domain
  ```

- If the SMTP ports stay blocked, use a provider's HTTP API instead: see [Optional: provider HTTP API with django-anymail](#optional-provider-http-api-with-django-anymail).

## Optional: provider HTTP API with django-anymail

The template sends through SMTP, which every provider offers. Switch to the provider's HTTPS API (port 443) when the VPS blocks outbound SMTP ports, or when you want the provider's webhooks for bounces and complaints. [django-anymail](https://anymail.dev/) does it and supports Django 6.1's `MAILERS`. This is a recipe: the template does not ship the dependency.

1. On the host, `uv add --no-sync "django-anymail[postmark]==15.2"`, with your provider's extra (`amazon-ses`, `brevo`, `mailgun`, `postmark`, `resend`, ...), then `uv sync --locked` in the dev container.
2. Add `"anymail"` to `THIRD_PARTY_APPS` in `config/settings/base.py`. Anymail asks for it always, and its webhook views need it.
3. Add the key to `ProductionEnv` in `config/settings/env.py` (`DJANGO_POSTMARK_SERVER_TOKEN: SecretStr`, required). In `config/settings/production.py`, replace the SMTP `MAILERS["default"]` with the provider's backend and read the key from `_env`. Option names differ per provider: Postmark takes `server_token`, Mailgun `api_key` and `sender_domain`, and Amazon SES uses boto3's credentials. Check your provider's page on anymail.dev.

   ```python
   MAILERS: dict[str, dict[str, Any]] = {
       "default": {
           "BACKEND": "anymail.backends.postmark.EmailBackend",
           "OPTIONS": {
               "server_token": _env.DJANGO_POSTMARK_SERVER_TOKEN.get_secret_value()
           },
       },
   }
   ```

   Keep the `MAILERS["admin-logging"]` block below it. It copies the default backend and adds `fail_silently`, so a provider outage during an error report never turns a 500 into a dropped connection.
4. Add the new variable to `docker/prod/.env.example`, and remove the `DJANGO_MAILER_*` SMTP variables you no longer use. The production guard (`config/settings/guards.py`) already accepts `anymail.*` backends; it still requires a real From address and `FRONTEND_URL`.
5. Optionally, mount `anymail.urls` for the provider's tracking webhooks (with the webhook secret the provider page describes), and stop sending to addresses that bounced or complained.

## Optional: sending email through Celery

The template has a Celery worker ([tasks.md](tasks.md)), and it does not use it for email. A verification or reset code is a secret that works once, and a task's arguments are stored in Valkey, in its append-only file on disk, until a worker takes them. Sent inside the request, the code only exists in memory and in the SMTP conversation.

Send through Celery when the email carries no secret and may wait: notifications, digests, receipts, anything in bulk. The request renders the message, and the task only sends the text:

```python
# apps/users/tasks.py
from __future__ import annotations

import smtplib

from celery import shared_task
from django.core.mail import EmailMessage


@shared_task(
    name="users.send_rendered_email",
    autoretry_for=(smtplib.SMTPException, OSError),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
)
def send_rendered_email(
    *,
    to: list[str],
    subject: str,
    body: str,
    from_email: str,
) -> None:
    EmailMessage(subject=subject, body=body, from_email=from_email, to=to).send()
```

A service enqueues it with `core.tasks.enqueue_on_commit(send_rendered_email, to=[...], subject=..., body=..., from_email=...)`: the email leaves after the commit, and never on a rollback.

To move allauth's emails as well, knowing that each code then waits in Valkey for a moment, replace the `try` block of `AccountAdapter.send_mail` (`apps/users/adapters.py`):

```python
        # Rendered here, in the request: the worker gets the finished text.
        message = self.render_mail(template_prefix, email, context)
        enqueue_on_commit(
            send_rendered_email,
            to=message.to,
            subject=message.subject,
            body=message.body,
            from_email=message.from_email,
        )
```

- The request no longer waits for SMTP, and a failed send is retried with backoff instead of being dropped.
- The email is sent when the request commits. Tests that read `mail.outbox` have to wrap the request in `django_capture_on_commit_callbacks(execute=True)`.
- If the broker is down the email is not sent: the error is logged, and the client asks for a new code.
- Treat Valkey as you treat the database: it now holds login codes, for as long as a message waits ([security.md](security.md#valkey-access)).

## Bounces and complaints

Watch the provider's dashboard. Repeated bounces and spam complaints hurt the domain's reputation, and with it every verification code you send. The rate limits on resend and password reset exist partly for this reason: per address, at most 20 password-reset emails and 10 verification codes an hour (`ACCOUNT_RATE_LIMITS` in `config/settings/base.py`), on top of allauth's per-minute and per-IP limits. Raise or lower them per project.

## Checklist

- [ ] `DJANGO_MAILER_HOST`, `DJANGO_MAILER_PORT`, TLS or SSL (not both), `DJANGO_MAILER_USERNAME`, `DJANGO_MAILER_PASSWORD`
- [ ] `DJANGO_DEFAULT_FROM_EMAIL` on the verified domain
- [ ] `FRONTEND_URL` set to the real frontend (it signs every email)
- [ ] SPF, DKIM and DMARC published
- [ ] `sendtestemail` received in Gmail and Outlook, and not in spam
