# ruff: noqa: E501
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.parse import urlsplit
from urllib.parse import urlunsplit

import dj_database_url
import django_stubs_ext
from corsheaders.defaults import default_headers
from django.core.exceptions import ImproperlyConfigured
from django.utils.csp import CSP

from .env import BaseEnv
from .env import read_env
from .env import required
from .guards import assert_throttle_rates

# Adds __class_getitem__ to Django generics such as QuerySet, so annotations
# like QuerySet[User] work at run time (django-stubs-ext README).
django_stubs_ext.monkeypatch()

BASE_DIR = Path(__file__).resolve(strict=True).parent.parent.parent
# Every variable this module reads, typed (config/settings/_env.py).
_env = read_env(BaseEnv)

DEBUG = _env.DJANGO_DEBUG
# The running release (the git commit in CI), stamped into the production image
# by the APP_RELEASE build arg. Sentry and JSON log lines report it.
APP_RELEASE = _env.APP_RELEASE
# Display and date-boundary zone. Data is always stored in UTC (USE_TZ=True).
TIME_ZONE = _env.DJANGO_TIME_ZONE
LANGUAGE_CODE = "en-us"
# English-only API; no translations are shipped.
USE_I18N = False
USE_TZ = True


if _env.DATABASE_URL is not None:
    default_database: dict[str, Any] = {
        **dj_database_url.parse(_env.DATABASE_URL.get_secret_value())
    }
else:
    default_database = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": required(_env.POSTGRES_DB, "POSTGRES_DB"),
        "USER": required(_env.POSTGRES_USER, "POSTGRES_USER"),
        "PASSWORD": required(
            _env.POSTGRES_PASSWORD, "POSTGRES_PASSWORD"
        ).get_secret_value(),
        "HOST": _env.POSTGRES_HOST,
        "PORT": _env.POSTGRES_PORT,
    }
DATABASES: dict[str, dict[str, Any]] = {"default": default_database}
# Give up on an unreachable server instead of hanging the request (and /health/).
DATABASES["default"].setdefault("OPTIONS", {})["connect_timeout"] = (
    _env.POSTGRES_CONNECT_TIMEOUT
)


def build_postgres_options(*, statement_timeout_ms: int, lock_timeout_ms: int) -> str:
    """
    Return the libpq `options` string for server-side timeouts (0 = no limit).

    Passed as DATABASES OPTIONS["options"], which takes precedence over PGOPTIONS.
    """
    parts = []
    if statement_timeout_ms > 0:
        parts.append(f"-c statement_timeout={statement_timeout_ms}")
    if lock_timeout_ms > 0:
        parts.append(f"-c lock_timeout={lock_timeout_ms}")
    return " ".join(parts)


# Valkey, the shared cache everywhere but tests. VALKEY_URL is redis:// or
# rediss://, with no credentials (VALKEY_PASSWORD) and no database number.
# See docs/ARCHITECTURE.md#caching.
VALKEY_URL = _env.VALKEY_URL
VALKEY_USERNAME = _env.VALKEY_USERNAME
VALKEY_PASSWORD = _env.VALKEY_PASSWORD.get_secret_value()
# The cache and the task broker use different databases of the same server.
VALKEY_CACHE_DB = _env.VALKEY_CACHE_DB
VALKEY_BROKER_DB = _env.VALKEY_BROKER_DB
VALKEY_SCHEMES = ("redis", "rediss")


def valkey_url(base_url: str, db: int) -> str:
    """
    Return `base_url` pointing at database `db`, keeping its scheme and query.

    Refuses credentials in the URL, which would reach logs and error reports,
    and any scheme other than redis:// or rediss://. Messages never echo the URL.
    """
    parts = urlsplit(base_url)
    if parts.scheme not in VALKEY_SCHEMES:
        msg = "VALKEY_URL must start with redis:// or rediss:// (not valkey://)."
        raise ImproperlyConfigured(msg)
    if "@" in parts.netloc:
        msg = "VALKEY_URL must not contain credentials: use VALKEY_PASSWORD."
        raise ImproperlyConfigured(msg)
    return urlunsplit(parts._replace(path=f"/{db}"))


def positive_seconds(name: str, value: int) -> int:
    """Return `value`, refusing zero or less: Valkey only evicts keys with a TTL."""
    if value <= 0:
        msg = f"{name} must be a positive number of seconds."
        raise ImproperlyConfigured(msg)
    return value


# Seconds to wait for a connection and for each reply. Short on purpose: a stalled
# Valkey must not hold a gunicorn thread.
VALKEY_CONNECT_TIMEOUT = _env.VALKEY_CONNECT_TIMEOUT
VALKEY_SOCKET_TIMEOUT = _env.VALKEY_SOCKET_TIMEOUT
# The TTL of a key written with timeout=None (30 days): Valkey evicts only keys
# that have one (core.cache_backend.ValkeyCache).
VALKEY_MAX_TTL = positive_seconds("VALKEY_MAX_TTL", _env.VALKEY_MAX_TTL)
# The project slug, so two projects never share keys. An empty value would leave
# the keys unprefixed, so it falls back to the default too.
CACHE_KEY_PREFIX = _env.DJANGO_CACHE_KEY_PREFIX or "project"
# Raise it to drop every entry after a change of format. Never derive it from
# APP_RELEASE: each deploy would reset throttles and rate limits.
CACHE_VERSION = _env.DJANGO_CACHE_VERSION
CACHE_TIMEOUT = positive_seconds("DJANGO_CACHE_TIMEOUT", _env.DJANGO_CACHE_TIMEOUT)


def build_valkey_cache(
    *,
    url: str,
    db: int,
    username: str,
    password: str,
) -> dict[str, Any]:
    """Return the CACHES entry for database `db` of the Valkey server at `url`."""
    options: dict[str, Any] = {
        "socket_connect_timeout": VALKEY_CONNECT_TIMEOUT,
        "socket_timeout": VALKEY_SOCKET_TIMEOUT,
        "retry_on_timeout": False,
        "health_check_interval": 30,
    }
    if username:
        options["username"] = username
    if password:
        # In OPTIONS, never in LOCATION: error reports hide a key named "password".
        options["password"] = password
    return {
        "BACKEND": "core.cache_backend.ValkeyCache",
        "LOCATION": valkey_url(url, db),
        "KEY_PREFIX": CACHE_KEY_PREFIX,
        "VERSION": CACHE_VERSION,
        "TIMEOUT": CACHE_TIMEOUT,
        "MAX_TTL": VALKEY_MAX_TTL,
        "OPTIONS": options,
    }


CACHES = {
    "default": build_valkey_cache(
        url=VALKEY_URL,
        db=VALKEY_CACHE_DB,
        username=VALKEY_USERNAME,
        password=VALKEY_PASSWORD,
    ),
}
# cached_db, never "cache" alone: a Valkey restart must sign nobody out.
# See docs/ARCHITECTURE.md#caching.
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"


# Background tasks. The broker is database VALKEY_BROKER_DB of the same Valkey.
def build_broker_url(*, url: str, db: int, username: str, password: str) -> str:
    """
    Return the broker URL: `url` on database `db`, with the credentials in it.

    Celery only takes the password inside the URL, so this value is a secret:
    it is built here, in memory, and never written to the environment or a log.
    Error reports hide it (core/debug.py), and Celery masks it where it prints it.
    """
    parts = urlsplit(valkey_url(url, db))
    if not (username or password):
        return urlunsplit(parts)
    credentials = f"{quote(username, safe='')}:{quote(password, safe='')}"
    return urlunsplit(parts._replace(netloc=f"{credentials}@{parts.netloc}"))


def check_task_time_limits(*, soft: int, hard: int, visibility_timeout: int) -> None:
    """
    Refuse limits under which a task that is still running is delivered again.

    With late acknowledgement the broker hands a message to another worker once
    `visibility_timeout` passes, so the hard limit must end the task before that.
    The soft limit comes first, so the task can record its failure.
    """
    if not 0 < soft < hard < visibility_timeout:
        msg = (
            "CELERY_TASK_SOFT_TIME_LIMIT must be below CELERY_TASK_TIME_LIMIT, and "
            f"both below the broker visibility timeout ({visibility_timeout} s)."
        )
        raise ImproperlyConfigured(msg)


# A full URL in CELERY_BROKER_URL replaces the one built from VALKEY_* (a project
# whose broker runs somewhere else). Celery itself reads that variable first.
CELERY_BROKER_URL = _env.CELERY_BROKER_URL.get_secret_value() or build_broker_url(
    url=VALKEY_URL,
    db=VALKEY_BROKER_DB,
    username=VALKEY_USERNAME,
    password=VALKEY_PASSWORD,
)
# Seconds an unacknowledged message stays invisible before it is delivered again.
CELERY_VISIBILITY_TIMEOUT = 3600
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "visibility_timeout": CELERY_VISIBILITY_TIMEOUT,
    # Longer than the cache's: a worker can wait, a web request cannot.
    "socket_connect_timeout": 2,
    "socket_timeout": 5,
    "socket_keepalive": True,
    "health_check_interval": 30,
}
# A web request that enqueues while the broker is down gives up after two quick
# retries instead of holding its thread (core.tasks.enqueue_on_commit logs it).
CELERY_TASK_PUBLISH_RETRY_POLICY = {
    "max_retries": 2,
    "interval_start": 0,
    "interval_step": 0.5,
    "interval_max": 1,
}
# The worker keeps Django's LOGGING (one JSON object per line in production)
# instead of replacing the root handler with Celery's plain-text one.
CELERY_WORKER_HIJACK_ROOT_LOGGER = False
# Both turn into the default in Celery 6; left unset, each logs a warning.
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_WORKER_CANCEL_LONG_RUNNING_TASKS_ON_CONNECTION_LOSS = True
# No result backend: the outcome of a task lives in the model it updates.
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
# Only the crontab schedules of beat read it; stored datetimes stay in UTC.
CELERY_TIMEZONE = TIME_ZONE
CELERY_ENABLE_UTC = True
# A message is acknowledged after its task ran, so a worker that dies in the middle
# does not lose it. Every task may therefore run twice and must be idempotent.
CELERY_TASK_ACKS_LATE = True
# A pool process killed mid-task (out of memory) sends the task again through
# retry(), bounded by max_retries. Not task_reject_on_worker_lost: it can loop.
CELERY_TASK_RETRY_ON_WORKER_LOST = True
# One message per process at a time: a long task does not sit on four others.
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
# Seconds. The soft limit raises SoftTimeLimitExceeded inside the task; the hard
# one kills its process.
CELERY_TASK_SOFT_TIME_LIMIT = _env.CELERY_TASK_SOFT_TIME_LIMIT
CELERY_TASK_TIME_LIMIT = _env.CELERY_TASK_TIME_LIMIT
check_task_time_limits(
    soft=CELERY_TASK_SOFT_TIME_LIMIT,
    hard=CELERY_TASK_TIME_LIMIT,
    visibility_timeout=CELERY_VISIBILITY_TIMEOUT,
)
CELERY_WORKER_CONCURRENCY = _env.CELERY_WORKER_CONCURRENCY
# A pool process is replaced after this many tasks, or once it holds this much
# memory (KiB): a leak in one task cannot grow forever.
CELERY_WORKER_MAX_TASKS_PER_CHILD = _env.CELERY_WORKER_MAX_TASKS_PER_CHILD
CELERY_WORKER_MAX_MEMORY_PER_CHILD = _env.CELERY_WORKER_MAX_MEMORY_PER_CHILD
# Seconds a stopping worker lets running tasks finish before it returns the rest
# to the queue. Keep it below the stop grace period of its container.
CELERY_WORKER_SOFT_SHUTDOWN_TIMEOUT = 20.0
# Periodic tasks. Nothing runs them unless a beat process is started.
CELERY_BEAT_SCHEDULE: dict[str, Any] = {}

# Background work goes through Celery: this backend runs nothing, so a stray
# django.tasks enqueue() shows. See docs/tasks.md#how-it-is-wired.
TASKS = {"default": {"BACKEND": "django.tasks.backends.dummy.DummyBackend"}}


ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

DJANGO_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.admin",
]
THIRD_PARTY_APPS = [
    "allauth",
    "allauth.account",
    "allauth.headless",
    "allauth.mfa",
    "corsheaders",
    "ninja",
    "axes",
]

LOCAL_APPS = [
    "core",
    "apps.users",
]
INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

AUTHENTICATION_BACKENDS = [
    # First, so a locked-out admin login is refused before the password is checked.
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
AUTH_USER_MODEL = "users.User"

PASSWORD_HASHERS = [
    # Django's Argon2 with the cost below (core/hashers.py).
    "core.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]
# Argon2 cost. Defaults are Django 6.1's (time 2, 102400 KiB, parallelism 8). Each
# concurrent hash allocates ARGON2_MEMORY_COST_KIB; on a 1-2 GB server consider the
# OWASP minimum (19456 KiB, time 2, parallelism 1). See docs/deploy.md#capacity.
ARGON2_TIME_COST = _env.DJANGO_ARGON2_TIME_COST
ARGON2_MEMORY_COST_KIB = _env.DJANGO_ARGON2_MEMORY_COST_KIB
ARGON2_PARALLELISM = _env.DJANGO_ARGON2_PARALLELISM
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    {"NAME": "core.password_validation.MaximumLengthValidator"},
]

MIDDLEWARE = [
    # Before SecurityMiddleware and CommonMiddleware: /health/ is probed over
    # plain HTTP at 127.0.0.1, which is not an allowed host and would redirect.
    "core.middleware.HealthCheckMiddleware",
    # Right after the probe, so every logged request has an id.
    "core.middleware.RequestIdMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    # Right after SecurityMiddleware, so CORS and 404 responses under /api/ get it too.
    "core.middleware.ApiNoStoreMiddleware",
    # After the response exists: rewrite HTML technical errors on the API when DEBUG.
    "core.middleware.ApiDebugErrorMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    # Inside CorsMiddleware, so its 400 still gets the CORS headers.
    "core.middleware.AllauthJsonDepthMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    # CommonMiddleware without trailing-slash redirects under /api/ (they are 404s).
    "core.middleware.ApiCommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    # Inside every header middleware (CSP, no-store, CORS, X-Frame-Options), so its
    # 405/406/415 responses get the same headers as Ninja's.
    "core.api.negotiation.ApiContractMiddleware",
    # Last: turns an admin lockout into a 429 response.
    "axes.middleware.AxesMiddleware",
]

STATIC_ROOT = str(BASE_DIR / "staticfiles")
STATIC_URL = "/static/"

# Uploads live in media/ at the repo root in development (/app/media in the image).
MEDIA_ROOT = _env.DJANGO_MEDIA_ROOT
# Can be an absolute URL for a separate media host (docs/NEXT-STEPS.md, nginx).
MEDIA_URL = _env.DJANGO_MEDIA_URL
# The host proxy reads uploads as another user: never leave modes to the umask.
# See docs/files.md#permissions.
FILE_UPLOAD_PERMISSIONS = 0o644
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o755
# Files only their owner may download (core.files.private_storage); never under
# MEDIA_ROOT. See docs/files.md#public-or-private.
PRIVATE_MEDIA_ROOT = _env.DJANGO_PRIVATE_MEDIA_ROOT
PRIVATE_MEDIA_ACCEL_REDIRECT_PREFIX = _env.DJANGO_PRIVATE_MEDIA_ACCEL_REDIRECT_PREFIX
STORAGES: dict[str, dict[str, Any]] = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "private": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {
            "location": PRIVATE_MEDIA_ROOT,
            # A path nothing serves: private files are only reachable through a
            # view that checks permissions (core.files.protected_file_response).
            "base_url": "/private-media-not-served/",
        },
    },
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
# Upload limits, at Django's defaults (docs/files.md#limits-and-content-type).
# The non-file part of a body; a larger JSON body is refused with 400 bad_request.
DATA_UPLOAD_MAX_MEMORY_SIZE = 2_621_440
# Larger uploaded files spool to a temporary file instead of memory.
FILE_UPLOAD_MAX_MEMORY_SIZE = 2_621_440
DATA_UPLOAD_MAX_NUMBER_FILES = 100
# Django has no global file-size cap: the hard cap per request is the proxy's
# client_max_body_size, and each file field validates its own size (core/files.py).

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # Email templates only (allauth overrides). This API renders no HTML pages.
        "DIRS": [str(BASE_DIR / "templates")],
        "APP_DIRS": True,
        "OPTIONS": {
            # Only what Django admin needs (admin.W411, E402, E404): this is a JSON API,
            # and Swagger builds its own context.
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
# Django's default, set explicitly: the browser leaves the admin and Swagger
# session cookies out of cross-site POSTs. API clients send X-Session-Token.
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
# Django's filter plus BROKER: the debug page and the error emails must not print
# a URL that embeds the Valkey password (core/debug.py).
DEFAULT_EXCEPTION_REPORTER_FILTER = "core.debug.SafeExceptionReporterFilter"

ADMIN_URL = "admin/"
# Comma-separated addresses, optionally '"Name" <email>' (names without commas).
# Empty: no error emails.
ADMINS = _env.DJANGO_ADMINS

# DJANGO_LOG_FORMAT: "text" (default here) or "json" (one object per line).
LOG_FORMATTERS = {
    "text": {
        "format": "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s",
    },
    "json": {"()": "core.logging.JsonFormatter", "release": APP_RELEASE},
}
# The worker writes through the same handler as the web process: both celery
# loggers need it. See docs/settings.md#logging.
LOGGERS_FOR_CELERY: dict[str, Any] = {
    "celery": {"handlers": ["console"], "level": "INFO", "propagate": False},
    "celery.task": {"handlers": ["console"], "level": "INFO", "propagate": False},
}
LOGGING: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"request_id": {"()": "core.logging.RequestIdFilter"}},
    "formatters": LOG_FORMATTERS,
    "handlers": {
        "console": {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
            "filters": ["request_id"],
            "formatter": _env.DJANGO_LOG_FORMAT,
        },
    },
    "root": {"level": "INFO", "handlers": ["console"]},
    "loggers": LOGGERS_FOR_CELERY,
}

# Brute-force lockout for the Django admin login only. allauth headless logins
# have their own rate limits (ACCOUNT_RATE_LIMITS) and must not be locked by axes.
AXES_ONLY_ADMIN_SITE = True
# AXES_ONLY_ADMIN_SITE alone still counts non-admin failures; see the callable.
AXES_WHITELIST_CALLABLE = "core.admin_lockout.is_outside_admin"
AXES_FAILURE_LIMIT = _env.AXES_FAILURE_LIMIT
AXES_COOLOFF_TIME = timedelta(minutes=15)
# Lock a username from one IP, so an attacker cannot lock a staff member out everywhere.
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_RESET_ON_SUCCESS = True
# The admin login form posts "username" (it holds the email for this user model).
AXES_USERNAME_FORM_FIELD = "username"
# Counters live in the default cache, shared by every worker in production.
AXES_HANDLER = "axes.handlers.cache.AxesCacheHandler"
# Reads TRUSTED_PROXY_COUNT, like Ninja throttles and allauth; see core/net.py.
AXES_CLIENT_IP_CALLABLE = "core.net.get_client_ip"

# Same in every environment, so Mailpit shows exactly what production sends.
DEFAULT_FROM_EMAIL = _env.DJANGO_DEFAULT_FROM_EMAIL
SERVER_EMAIL = (
    DEFAULT_FROM_EMAIL if _env.DJANGO_SERVER_EMAIL is None else _env.DJANGO_SERVER_EMAIL
)
EMAIL_SUBJECT_PREFIX = _env.DJANGO_EMAIL_SUBJECT_PREFIX
# Without it allauth prefixes subjects with the request host (no sites framework).
ACCOUNT_EMAIL_SUBJECT_PREFIX = EMAIL_SUBJECT_PREFIX
# Signature of the account emails (templates/account/email/base_message.txt).
EMAIL_SIGNATURE_NAME = _env.DJANGO_EMAIL_SIGNATURE_NAME

ACCOUNT_ALLOW_REGISTRATION = _env.DJANGO_ACCOUNT_ALLOW_REGISTRATION
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*"]
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED = True
ACCOUNT_PASSWORD_RESET_BY_CODE_ENABLED = True
# Two resends per pending flow, then 409. See docs/email.md#how-it-works.
ACCOUNT_EMAIL_VERIFICATION_SUPPORTS_RESEND = True
# Adding, removing or promoting an email asks for the password again (POST
# /_allauth/app/v1/auth/reauthenticate), even right after login: a stolen session
# token alone cannot take over the account. See docs/security.md#reauthentication.
ACCOUNT_REAUTHENTICATION_REQUIRED = True
# Hourly caps per address on top of allauth's defaults (merged per action). Every
# email costs money and an unwanted one hurts the domain's reputation; without
# them one IP could send 300 reset emails an hour to the same address.
ACCOUNT_RATE_LIMITS = {
    "reset_password": "20/m/ip,5/m/key,20/h/key",
    "confirm_email": "1/10s/key,10/h/key",
}
ACCOUNT_ADAPTER = "apps.users.adapters.AccountAdapter"
# User payloads carry the public UUID as "id", not the integer pk.
HEADLESS_ADAPTER = "apps.users.adapters.HeadlessAdapter"
# allauth.mfa stays installed but offers no authenticator type, so its TOTP and
# recovery-code routes are not mounted; turn it on with docs/auth/mfa.md.
MFA_SUPPORTED_TYPES: list[str] = []
MFA_TOTP_ISSUER = EMAIL_SIGNATURE_NAME


def build_headless_frontend_urls(frontend_url: str) -> dict[str, str]:
    base_url = frontend_url.rstrip("/")
    # Codes, not links, verify emails and reset passwords. These two keys are the
    # frontend pages allauth links to from the "account already exists" and
    # "unknown account" emails. Re-add account_confirm_email and
    # account_reset_password_from_key if those flows switch back to links, and
    # socialaccount_login_error with an OAuth provider (docs/auth/oauth.md).
    return {
        "account_reset_password": f"{base_url}/account/password/reset",
        "account_signup": f"{base_url}/account/signup",
    }


HEADLESS_ONLY = True
HEADLESS_CLIENTS = ("app",)
FRONTEND_URL = _env.FRONTEND_URL.rstrip("/")
HEADLESS_FRONTEND_URLS = build_headless_frontend_urls(FRONTEND_URL)

# Proxies that append to X-Forwarded-For: the one count core.net.get_client_ip,
# Ninja throttles and allauth read. 0 trusts no proxy (REMOTE_ADDR only), as
# local and test need; production.py sets it from DJANGO_TRUSTED_PROXY_COUNT.
TRUSTED_PROXY_COUNT = 0

# NINJA_TITLE, NINJA_DESCRIPTION, NINJA_VERSION and NINJA_SERVERS are this
# project's settings: core/api/api.py passes them to NinjaAPI. Ninja itself reads
# the pagination, NUM_PROXIES and throttle-rate aliases (ninja/conf.py in 1.8.0).
# The throttles take the client IP from core.net (TRUSTED_PROXY_COUNT, above);
# NINJA_NUM_PROXIES keeps Ninja's own get_ident in step with it, and
# production.py sets both from DJANGO_TRUSTED_PROXY_COUNT.
NINJA_TITLE = "Project API"
NINJA_DESCRIPTION = (
    "The Project JSON API. Account endpoints under /_allauth/ belong to "
    "django-allauth and keep its own format (docs/api.md)."
)
NINJA_VERSION = "1.0.0"
NINJA_SERVERS = [{"url": "/", "description": "Current host"}]
NINJA_PAGINATION_PER_PAGE = 20
NINJA_MAX_PER_PAGE_SIZE = 100
NINJA_PAGINATION_CLASS = "core.api.pagination.LimitPageNumberPagination"
NINJA_NUM_PROXIES = TRUSTED_PROXY_COUNT
NINJA_DEFAULT_THROTTLE_RATES = {
    "anon": _env.DJANGO_THROTTLE_ANON_RATE,
    "user": _env.DJANGO_THROTTLE_USER_RATE,
    # Scoped rates go here as "<app>.<action>": "5/min".
}
# A bad rate is a 500 on every request: refuse it at startup.
assert_throttle_rates(NINJA_DEFAULT_THROTTLE_RATES)

# Deepest page LimitPageNumberPagination serves; deeper pages are a 404.
# Env var stays DJANGO_API_MAX_PAGE_NUMBER; the setting is framework-neutral.
API_MAX_PAGE_NUMBER = _env.DJANGO_API_MAX_PAGE_NUMBER

CORS_URLS_REGEX = r"^/(api|_allauth)/.*$"
CORS_ALLOWED_ORIGINS = _env.CORS_ALLOWED_ORIGINS
CORS_ALLOW_HEADERS = (
    *default_headers,
    "x-session-token",
    "x-email-verification-key",
    "x-password-reset-key",
)

# For the admin and Swagger UI, which run with staff sessions; styles allow
# 'unsafe-inline' for the admin's style attributes, scripts never do.
# See docs/security.md#content-security-policy.
SECURE_CSP = {
    "default-src": [CSP.SELF],
    "script-src": [CSP.SELF],
    "style-src": [CSP.SELF, CSP.UNSAFE_INLINE],
    "img-src": [CSP.SELF, "data:"],
    "object-src": [CSP.NONE],
    "base-uri": [CSP.NONE],
    "form-action": [CSP.SELF],
    "frame-ancestors": [CSP.NONE],
}
