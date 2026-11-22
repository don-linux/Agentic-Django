# Security

## Production secrets

The production container refuses to start while a secret still has an example or weak value (`config/settings/guards.py`, called at the end of `config/settings/production.py`):

- `DJANGO_SECRET_KEY` and every `DJANGO_SECRET_KEY_FALLBACKS` entry need at least 50 characters, 5 different characters, no `change-me`, and no `django-insecure-` prefix (that prefix marks the development and test keys in `local.py` and `test.py`).
- `DJANGO_ADMIN_URL` must not contain `change-me`, must not be `admin/`, must end with `/`, and must not start with a path the app serves (`api/`, `_allauth/`, `static/`, `media/`, `health/`): `config/urls.py` mounts the admin first, so it would hide those routes.
- `POSTGRES_PASSWORD` must not be empty or contain `change-me` (placeholders are matched in any case). A `DATABASE_URL` may carry the password instead, or none at all for peer authentication.
- `VALKEY_URL` and `VALKEY_PASSWORD` are both required. The URL starts with `redis://` or `rediss://` and carries no credentials; a `rediss://` URL must end with `?ssl_cert_reqs=required`. The password has at least 32 characters, no `change-me`, and no quote, backslash or line break (Compose writes it into `valkey.conf`). `VALKEY_CACHE_DB` and `VALKEY_BROKER_DB` must differ.
- Celery must reach a real worker: eager mode off, and a broker on `redis://` or `rediss://`. A `rediss://` broker must verify the certificate (`ssl_cert_reqs=required`), also when `CELERY_BROKER_URL` is set by hand.
- `DJANGO_ALLOWED_HOSTS` must be set, and no entry may contain `*` or be an example domain (`example.com`, `example.org`, `example.net` or a subdomain of one) or `localhost`. Reserved test names (`.invalid`, `.test`) are accepted, for CI and tests.
- Each `DJANGO_CSRF_TRUSTED_ORIGINS` and `CORS_ALLOWED_ORIGINS` entry is an `https://` origin with no path, not an example domain or `localhost`. Both are the frontend's origin, like `FRONTEND_URL`; both may stay empty for token-only clients.
- `DJANGO_DEFAULT_FROM_EMAIL` and `FRONTEND_URL` must be real: not empty, not an example domain, and `FRONTEND_URL` uses `https://` ([email.md](email.md#how-it-works)).
- HSTS must stay valid as it ramps ([HSTS](#hsts)).

The error names the variable and never prints its value. Generate a key with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

The guards in `config/settings/guards.py` import without any production variable, so tests can call them directly, and their messages name the variable without ever printing its value.

## Rotating SECRET_KEY

`SECRET_KEY` signs sessions (allauth's `X-Session-Token` is a session key), password reset tokens, and other signed values. Replacing it outright signs everyone out and invalidates pending links. To rotate it without that, for example after a leak:

1. Generate a new key.
2. Set it as `DJANGO_SECRET_KEY` and move the old one to `DJANGO_SECRET_KEY_FALLBACKS` (comma separated if there are several).
3. Deploy. New values are signed with the new key; values signed with the old key are still accepted.
4. Wait at least `SESSION_COOKIE_AGE` (two weeks by default) and the longest token lifetime you rely on (`PASSWORD_RESET_TIMEOUT`, three days by default).
5. Remove the old key from `DJANGO_SECRET_KEY_FALLBACKS` and deploy again.

After a leak, a fallback also keeps whatever the attacker signed valid until it is removed. If that risk is higher than signing everyone out, skip the fallback.

## Passwords

Passwords are hashed with Argon2 (`core/hashers.py`), and every new password goes through Django's validators plus `core.password_validation.MaximumLengthValidator`, which refuses more than 256 characters (`password_too_long`). The cap applies when a password is set, not inside the hasher, so a login attempt of any length is still hashed; `DATA_UPLOAD_MAX_MEMORY_SIZE` bounds the body. The cap only stops absurd inputs: the cost of a hash comes from `DJANGO_ARGON2_MEMORY_COST_KIB`, whatever the length, and a login for an unknown email still runs one full hash. What limits password guessing and hashing load is allauth's login rate limits and the host sizing in [deploy.md#capacity](deploy.md#capacity).

## Reauthentication

`ACCOUNT_REAUTHENTICATION_REQUIRED` is on: adding, removing or promoting an email answers 401 with a pending `reauthenticate` flow until the client sends the password to `POST /_allauth/app/v1/auth/reauthenticate`. A fresh login does not count; only an explicit reauthentication does, for `ACCOUNT_REAUTHENTICATION_TIMEOUT` (300 seconds). Without it, a stolen `X-Session-Token` could add an attacker's address, verify it, make it primary and then reset the password.

## Error tracking

Sentry is off unless `SENTRY_DSN` is set (`config/settings/production.py` calls `core.sentry.init_sentry`). When it is on:

- `send_default_pii=False`: no user emails, IPs, or cookies are attached by the SDK.
- Credential headers are replaced with `[Filtered]`: allauth headless's `X-Session-Token`, `X-Email-Verification-Key`, and `X-Password-Reset-Key`, plus `Authorization`, `Cookie`, and `X-CSRFToken`. sentry-sdk's built-in list does not know the allauth headers.
- Body fields such as `password`, `code`, `key`, and `token` are filtered at any depth, and cookies are dropped.
- In every part of an event (local variables, exception and log messages, breadcrumbs, task data), a value whose name contains `password`, `token` or `secret` is filtered (`api_token`, `DB_PASSWORD`), and the credentials of every URL are masked: `redis://:<password>@host` is sent as `redis://[Filtered]@host`. The Celery broker URL carries the Valkey password, so this covers it under any variable name.
- Task arguments are never sent: with PII off the SDK replaces them whole.
- What the filter cannot see: a secret under a name without those words and outside a URL, inside the text form of an object, or in a query string. Do not hold secrets in such variables in code that can raise.
- Events carry the request id (`X-Request-ID`), so a Sentry event matches its log lines.

Every new custom authentication or secret header must be added to `core.sentry.SENSITIVE_HEADERS`.

- URL credentials (`redis://:<password>@host`, `https://user:password@host`) are stripped up to the last `@` before the path, so an email inside a path stays.
- `_meta` and `modules` are not scrubbed (`pytokens` is a package name, not a token).
- `CeleryIntegration` is listed explicitly and its beat monitors stay off. Never pass `data_collection={"queues": True}`: it would send task arguments.

Set `SENTRY_DSN` (and optionally `SENTRY_ENVIRONMENT` and `SENTRY_TRACES_SAMPLE_RATE`) in `docker/prod/.env`; with an empty DSN nothing is sent.

## Valkey access

Valkey holds the sessions, which are credentials, and values that Django reads back with pickle. Whoever can write to it can sign in as any user and run code in the django container, so it is protected like the database:

- **No published port.** In `docker/prod/compose.yaml` only the services of the compose project reach it, and `core/tests/test_compose.py` fails if a port is added. Development publishes it on `127.0.0.1` only.
- **A password, always.** Production refuses to start without `VALKEY_PASSWORD` (32 characters or more). It never goes inside `VALKEY_URL`: settings add it in memory, and error reports and Celery print it masked. Compose writes it to a config file on a tmpfs, so it does not show in `ps`.
- **TLS outside the compose network.** For a managed Valkey, or one on another host, use `rediss://<host>:<port>?ssl_cert_reqs=required`. `rediss://` turns TLS on. `ssl_cert_reqs=required` makes the Celery broker verify the server's certificate: without it the broker would connect without verifying and only log a warning, so production refuses to start. The check covers `VALKEY_URL` and a `CELERY_BROKER_URL` set by hand, and accepts only the lowercase value `required`, the one both clients understand. A URL that turns the host name check off (`ssl_check_hostname=false`, or `0`, `f`, `n`, `no`, which the cache reads the same way) is refused too: a certificate would pass whatever server it was issued for. The cache always verifies.
- **Not in Sentry.** The SDK's Redis integration stays disabled: it would send cache keys, and the cache key of a session contains the session token.
- **Task arguments are stored there** until a worker takes them, and they are written to the append-only file. Pass ids, never secrets ([tasks.md](tasks.md#writing-a-task)).

- sentry-sdk enables `RedisIntegration` by itself once redis-py is installed, and it reports cache keys even with PII off, so `core/sentry.py` disables it explicitly.
- redis-py and Celery speak only `redis://` and `rediss://`; production refuses `valkey://`.
- A parameter repeated in the URL is judged by all its values: the cache reads the first one and the broker the last.
- The Valkey image drops root only when its command is `valkey-server`. The production command is a shell that writes the config first, so the service runs as uid 999 itself.

## Valkey outages

Valkey holds the API throttles, the allauth rate limits and the django-axes counters. `core.cache_backend.ValkeyCache` decides what happens while it is unreachable:

- **Reads and writes fail open.** The cache behaves as if it were empty, so a throttle or a rate limit stops counting instead of answering 500 on every request. Until Valkey is back, brute force is slowed only by Argon2, where each guess costs a full hash. Treat an outage as urgent.
- **It is loud.** Each process logs a WARNING on the `core.cache` logger, at most one every 30 seconds, and Sentry keeps it as a breadcrumb.
- **Readiness shows it.** `GET /health/` answers 503 `cache unavailable` while Valkey does not answer, so the container is unhealthy and a proxy that checks it stops sending traffic. `/health/live/` keeps answering 200.
- **Deletes raise.** A value that could not be removed must not look removed, so `delete`, `delete_many` and `clear` propagate the error. Deactivating a user deletes that user's sessions inside its transaction, so the whole deactivation rolls back.
- **Logout fails.** During an outage `DELETE /_allauth/app/v1/auth/session` answers 500, because the cached copy of the session cannot be removed. Django has already deleted the session row by then, so once Valkey is back the token works again from the cached copy, until the client logs out again or the session expires. Clients must retry a failed logout.
- **One exception.** allauth and django-axes delete their own rate-limit keys on every login (the `allauth:rl:` and `axes-` prefixes). Those deletes fail open: otherwise every login would be a 500 during an outage. Losing one only leaves a limit stricter until the key expires. The exception covers `delete` only: `ValkeyCache` does not override `delete_many` or `clear`, so both always raise during an outage.

A connection gives up after 1 second (`VALKEY_CONNECT_TIMEOUT`, `VALKEY_SOCKET_TIMEOUT`), so a stalled Valkey delays a request instead of holding a gunicorn thread.

On an outage `add` returns `True`, as on an empty cache; `False` would make allauth answer 429 to every login. After a failed logout allauth may raise `UpdateError` while unwinding, because it saves the session after Django has already deleted the row. A headless token is a Django session and the user id lives only in each session's encoded data, so deactivating a user decodes every unexpired session.

## HTTPS

Production redirects HTTP to HTTPS (`DJANGO_SECURE_SSL_REDIRECT`, default true), names the cookies `__Secure-sessionid` and `__Secure-csrftoken` and marks them `Secure`, and trusts `X-Forwarded-Proto` from the host proxy (`SECURE_PROXY_SSL_HEADER`). That header is only safe while Gunicorn is reachable through the proxy alone ([deploy.md#proxy](deploy.md#proxy)).

### HSTS

`DJANGO_SECURE_HSTS_SECONDS` starts at 60 seconds. Raise it to `31536000` once HTTPS works on every page, and only then turn on `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS` and `DJANGO_SECURE_HSTS_PRELOAD`. Browsers remember the policy for the whole period, and the preload list is hard to leave.

Production enforces that order: it refuses a max-age of 0 or less, either flag below one year, and preload without includeSubDomains. Django's own checks only ask for the flags (`security.W005`, `security.W021`), so production silences each one while its flag is still off, and `check --deploy --fail-level WARNING` passes at every step of the ramp.

## Exception reports

Django's `SafeExceptionReporterFilter` already hides settings whose names contain `PASS`, `KEY`, `SECRET` or `TOKEN`, so every `VALKEY_*` value is covered. The template adds `BROKER`, because a broker URL embeds the Valkey password, and `DSN`, because `SENTRY_DSN` embeds the project's Sentry key.

## Admin lockout

django-axes guards only the Django admin login: 5 failures for one username and IP lock that pair for 15 minutes (`AXES_*` in `config/settings/base.py`). allauth's headless logins have their own rate limits.

- `axes.backends.AxesStandaloneBackend` is first in `AUTHENTICATION_BACKENDS`, so a locked-out admin login is refused before the password is checked, and `AxesMiddleware` is last in `MIDDLEWARE`, where it turns a lockout into a 429.
- `AXES_ONLY_ADMIN_SITE` alone still records failures outside the admin. Without `AXES_WHITELIST_CALLABLE = core.admin_lockout.is_outside_admin`, allauth's JSON login could get a plain-text 429, and failed API logins could lock the admin.
- The admin login form posts the field `username`, even though it holds the email.

### Admin permissions

Only a superuser grants or removes privileges. For staff without `is_superuser`, the user admin shows `is_staff`, `is_superuser`, groups and permissions read-only, refuses to change any staff or superuser account (their own included), and its "Deactivate selected users" and "Reactivate selected users" actions skip them. One gap remains in Django's group admin: a staff user who may change groups (`auth.change_group`) can add permissions to a group they belong to. Grant that permission only to superusers.

## Recipe: encrypting sensitive fields

Opt-in, for a project that stores national IDs, tax IDs, TOTP secrets, or third-party API tokens. The template ships no encryption code, setting, or dependency. Encrypted columns cannot be filtered, ordered, or indexed by value: look them up by a separate search hash.

### Option A: a small helper (recommended)

Add `cryptography` as a direct dependency, pinned to the version already in `uv.lock` (`uv add --no-sync "cryptography==50.0.2"` on the host), and two settings read from the environment:

```python
# config/settings/base.py
# Comma separated Fernet keys: the first one encrypts, all of them decrypt.
FIELD_ENCRYPTION_KEYS = env.list("DJANGO_FIELD_ENCRYPTION_KEYS", default=[])
# HMAC key for search hashes (lookups and unique constraints on encrypted values).
SEARCH_HASH_KEY = env("DJANGO_SEARCH_HASH_KEY", default="")
```

```python
# core/crypto.py
import hashlib
import hmac
from collections.abc import Callable
from functools import cache

from cryptography.fernet import Fernet
from cryptography.fernet import MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


@cache
def _fernet() -> MultiFernet:
    # Raises only when the helper is used, so projects without encrypted fields
    # need no keys. Tests that override the keys call _fernet.cache_clear().
    if not settings.FIELD_ENCRYPTION_KEYS:
        msg = "DJANGO_FIELD_ENCRYPTION_KEYS is empty (docs/security.md)."
        raise ImproperlyConfigured(msg)
    return MultiFernet([Fernet(key) for key in settings.FIELD_ENCRYPTION_KEYS])


def encrypt_str(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_str(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


def search_hash(value: str, *, normalize: Callable[[str], str] = str.strip) -> str:
    """HMAC-SHA256 of the normalized value, for exact lookups (64 hex characters)."""
    if not settings.SEARCH_HASH_KEY:
        msg = "DJANGO_SEARCH_HASH_KEY is empty (docs/security.md)."
        raise ImproperlyConfigured(msg)
    digest = hmac.new(
        settings.SEARCH_HASH_KEY.encode(), normalize(value).encode(), hashlib.sha256
    )
    return digest.hexdigest()
```

```python
# core/fields.py
from typing import Any

from django.db import models

from core.crypto import decrypt_str
from core.crypto import encrypt_str


class EncryptedTextField(models.TextField):
    """Stores Fernet ciphertext and reads back plaintext. Never filter on it."""

    def get_prep_value(self, value: Any) -> Any:
        value = super().get_prep_value(value)
        if value in (None, ""):
            return value
        return encrypt_str(value)

    def from_db_value(self, value: Any, expression: Any, connection: Any) -> Any:
        if value in (None, ""):
            return value
        return decrypt_str(value)
```

`dumpdata` and Django's serializers read the decrypted value, so a dump of a model with an encrypted field holds plaintext: exclude those models from dumps (`--exclude customers.Customer`) or treat the dump as a secret.

A model keeps the hash next to the encrypted value, and the service sets both:

```python
# apps/customers/models.py
class Customer(BaseModel):
    tax_id = EncryptedTextField()
    tax_id_hash = models.CharField(max_length=64, unique=True, editable=False)


# apps/customers/services.py
def _normalize_tax_id(value: str) -> str:
    return value.strip().upper()


@transaction.atomic
def customer_create(*, tax_id: str) -> Customer:
    customer = Customer(
        tax_id=tax_id,
        tax_id_hash=search_hash(tax_id, normalize=_normalize_tax_id),
    )
    customer.full_clean()
    customer.save()
    return customer


# apps/customers/selectors.py
def customer_get_by_tax_id(*, tax_id: str) -> Customer:
    return Customer.objects.get(
        tax_id_hash=search_hash(tax_id, normalize=_normalize_tax_id)
    )
```

Generate the keys once per environment. They differ from each other and from `SECRET_KEY`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # DJANGO_FIELD_ENCRYPTION_KEYS
python -c "import secrets; print(secrets.token_urlsafe(64))"  # DJANGO_SEARCH_HASH_KEY
```

**Backups:** store the keys apart from the database dumps. A dump without its keys cannot be read, and a dump stored next to its keys makes the encryption pointless.

**Rotation:**

1. Prepend the new key to `DJANGO_FIELD_ENCRYPTION_KEYS` and deploy: new writes use it, and old values still decrypt.
2. Re-encrypt in batches with a management command. Loading a row decrypts it with whichever key matches, and saving encrypts it with the first key, which is what `MultiFernet.rotate()` does token by token. `bulk_update()` leaves `updated_at` alone, which suits a re-encryption.

   ```python
   # apps/customers/management/commands/reencrypt_customers.py
   from django.core.management.base import BaseCommand
   from django.db import transaction

   from apps.customers.models import Customer

   BATCH_SIZE = 500


   class Command(BaseCommand):
       help = "Re-encrypt Customer.tax_id with the first DJANGO_FIELD_ENCRYPTION_KEYS key."

       def handle(self, *args: object, **options: object) -> None:
           # Pages by primary key instead of iterator(): no server-side cursor
           # stays open across the batches' transactions.
           last_pk = 0
           while batch := list(
               Customer.objects.only("pk", "tax_id")
               .filter(pk__gt=last_pk)
               .order_by("pk")[:BATCH_SIZE]
           ):
               self._save(batch)
               last_pk = batch[-1].pk

       @staticmethod
       def _save(batch: list[Customer]) -> None:
           with transaction.atomic():
               Customer.objects.bulk_update(batch, ["tax_id"])
   ```

3. Remove the old key and deploy again.

The search-hash key cannot rotate without recomputing every `*_hash` column from the plaintext. Pick it once and keep it.

**Testing tips:** round-trip a value through save and reload; prepend a new key and check that a value encrypted with the old key still reads; check that the normalized hash is stable (`" ab12 "` and `"AB12"` give the same hash); call `_fernet.cache_clear()` when a test overrides the keys.

### Option B: django-fernet-encrypted-fields

This package **does not read a `FIELD_ENCRYPTION_KEY` setting**. It derives its keys with PBKDF2 from `SECRET_KEY` (plus `SECRET_KEY_FALLBACKS`) and `SALT_KEY`, which can be a list so salts rotate like keys. Consequences:

- Rotating or losing `SECRET_KEY` without keeping the old value in `SECRET_KEY_FALLBACKS` makes every encrypted value unreadable. The rotation steps in [Rotating SECRET_KEY](#rotating-secret_key) must keep the old key as long as rows encrypted with it exist.
- `SECRET_KEY` and `SALT_KEY` must both be backed up, apart from the database.

Option A keeps dedicated keys, so `SECRET_KEY` rotation never touches encrypted data.

## Content Security Policy

Django's built-in CSP (`django.middleware.csp.ContentSecurityPolicyMiddleware` and `SECURE_CSP` in `config/settings/base.py`) protects the HTML pages that run with staff sessions: the admin and Swagger UI at `/api/v1/docs/`. JSON responses carry the header too, where it is harmless.

- Scripts load only from the site (`script-src 'self'`), with no plugins (`object-src 'none'`), no `<base>` tag, forms posting only to the site, and no framing (`frame-ancestors 'none'`).
- Swagger UI at `/api/v1/docs/` is staff-only. Django Ninja 1.8.0 still renders inline style and script on that page, so `SECURE_CSP` allows `'unsafe-inline'` for it. A later change should switch the page to hashes or nonces once the upstream template allows it. Do not load Swagger assets from a CDN.
- Styles allow `'unsafe-inline'`: Swagger's template has an inline `<style>`, and the admin uses `style` attributes.
- A view that needs another policy uses `django.views.decorators.csp.csp_override({...})`, which replaces the whole policy for that view. To allow one inline script instead, add `CSP.NONCE` to `script-src`, the `django.template.context_processors.csp` context processor, and `nonce="{{ csp_nonce }}"` on the tag.
- `core/tests/http/test_csp.py` fails if the admin gains an inline script. `core/tests/ninja/test_csp.py` pins the docs page to `frame-ancestors 'none'` and `object-src 'none'`, and records the current `'unsafe-inline'` allowance until hashes or nonces are in place.

nginx sets its own, stricter policy for `/media/` and `/protected-media/` ([NEXT-STEPS.md](NEXT-STEPS.md)).

Swagger UI is staff-only and served by the application, not a CDN. The current policy still allows `'unsafe-inline'` for Ninja's docs page (see above); tightening that is a follow-up, not a blocker for this contract.
