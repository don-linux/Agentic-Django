# ruff: noqa: E501
import importlib.util
from typing import Any

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import ADMINS
from .base import APP_RELEASE
from .base import CELERY_BROKER_URL
from .base import CORS_ALLOWED_ORIGINS
from .base import DATABASES
from .base import DEFAULT_FROM_EMAIL
from .base import LOG_FORMATTERS
from .base import LOGGERS_FOR_CELERY
from .base import STORAGES
from .base import VALKEY_BROKER_DB
from .base import VALKEY_CACHE_DB
from .base import build_headless_frontend_urls
from .base import build_postgres_options
from .env import ProductionEnv
from .env import read_env
from .env import required
from .guards import assert_production_celery
from .guards import assert_production_email
from .guards import assert_production_hosts
from .guards import assert_production_hsts
from .guards import assert_production_secrets
from .guards import assert_production_valkey
from .guards import silenced_hsts_system_checks

_env = read_env(ProductionEnv)

DEBUG = False
SECRET_KEY = _env.DJANGO_SECRET_KEY.get_secret_value()
# Old keys still accepted for sessions and signed tokens while rotating.
SECRET_KEY_FALLBACKS = [
    key.get_secret_value() for key in _env.DJANGO_SECRET_KEY_FALLBACKS
]
# Required: no example.com default. The startup guard rejects example and localhost hosts.
ALLOWED_HOSTS = _env.DJANGO_ALLOWED_HOSTS

DATABASES["default"]["CONN_MAX_AGE"] = _env.CONN_MAX_AGE
# Persistent connections go stale when Postgres restarts; check before reusing one.
DATABASES["default"]["CONN_HEALTH_CHECKS"] = _env.CONN_HEALTH_CHECKS
# A runaway query or a stuck lock must not hold a gunicorn thread forever.
# start.sh migrates with no statement timeout and a lock timeout instead.
_postgres_options = build_postgres_options(
    statement_timeout_ms=_env.POSTGRES_STATEMENT_TIMEOUT_MS,
    lock_timeout_ms=_env.POSTGRES_LOCK_TIMEOUT_MS,
)
if _postgres_options:
    DATABASES["default"]["OPTIONS"]["options"] = _postgres_options

# Both are required here. base.py has already built CACHES from them, but with a
# localhost default and an empty password that production must never fall back to.
VALKEY_URL = _env.VALKEY_URL
VALKEY_PASSWORD = _env.VALKEY_PASSWORD.get_secret_value()

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# Proxies that append to X-Forwarded-For: one count for core.net (django-axes
# and the API throttles), allauth's rate limits and Ninja. See
# docs/api.md#throttling.
TRUSTED_PROXY_COUNT = _env.DJANGO_TRUSTED_PROXY_COUNT
ALLAUTH_TRUSTED_PROXY_COUNT = TRUSTED_PROXY_COUNT
NINJA_NUM_PROXIES = TRUSTED_PROXY_COUNT
FRONTEND_URL = _env.FRONTEND_URL.rstrip("/")
HEADLESS_FRONTEND_URLS = build_headless_frontend_urls(FRONTEND_URL)
SECURE_SSL_REDIRECT = _env.DJANGO_SECURE_SSL_REDIRECT
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_NAME = "__Secure-sessionid"
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_NAME = "__Secure-csrftoken"
CSRF_TRUSTED_ORIGINS = _env.DJANGO_CSRF_TRUSTED_ORIGINS
# Browsers cache it: keep 60 until HTTPS works everywhere. See docs/security.md#hsts.
SECURE_HSTS_SECONDS = _env.DJANGO_SECURE_HSTS_SECONDS
SECURE_HSTS_INCLUDE_SUBDOMAINS = _env.DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS
SECURE_HSTS_PRELOAD = _env.DJANGO_SECURE_HSTS_PRELOAD
# W005/W021 stay silent only while their flag is off. See docs/security.md#hsts.
_silenced_hsts = silenced_hsts_system_checks(
    include_subdomains=SECURE_HSTS_INCLUDE_SUBDOMAINS,
    preload=SECURE_HSTS_PRELOAD,
)
if _silenced_hsts:
    SILENCED_SYSTEM_CHECKS = _silenced_hsts
SECURE_CONTENT_TYPE_NOSNIFF = _env.DJANGO_SECURE_CONTENT_TYPE_NOSNIFF

# base.py defines "default" and "private"; production only compresses static files.
STORAGES["staticfiles"] = {
    "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
}

# Optional object storage: DJANGO_STORAGE_BACKEND=s3 ("local" or "s3"), with an
# image built with the s3 extra. See docs/files.md#s3-compatible-storage-optional.
STORAGE_BACKEND = _env.DJANGO_STORAGE_BACKEND
if STORAGE_BACKEND == "s3":
    if importlib.util.find_spec("storages") is None:
        msg = "DJANGO_STORAGE_BACKEND=s3 requires an image built with DOCKER_PROD_UV_EXTRAS=s3"
        raise ImproperlyConfigured(msg)
    _s3_options = {
        "bucket_name": required(
            _env.DJANGO_AWS_STORAGE_BUCKET_NAME, "DJANGO_AWS_STORAGE_BUCKET_NAME"
        ),
        "region_name": _env.DJANGO_AWS_S3_REGION_NAME,
        "endpoint_url": _env.DJANGO_AWS_S3_ENDPOINT_URL,
        # Never replace an object that has the same name; files stay immutable.
        "file_overwrite": False,
        # Access comes from the bucket policy (public/ readable), not object ACLs.
        "default_acl": None,
        "signature_version": "s3v4",
    }
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            **_s3_options,
            "location": "public",
            "querystring_auth": False,
            "custom_domain": _env.DJANGO_AWS_S3_CUSTOM_DOMAIN,
        },
    }
    # Private files: short-lived signed URLs (core.files.protected_file_response).
    STORAGES["private"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            **_s3_options,
            "location": "private",
            "querystring_auth": True,
            "querystring_expire": _env.DJANGO_AWS_QUERYSTRING_EXPIRE,
        },
    }

# From and subject prefix are shared with every environment (config/settings/base.py).
# DJANGO_MAILER_HOST is required: verification is mandatory, so without SMTP nobody can sign up.
MAILERS: dict[str, dict[str, Any]] = {
    "default": {
        "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "OPTIONS": {
            "host": _env.DJANGO_MAILER_HOST,
            # Unset: the standard port for the protocol, 587 with TLS and 465 with SSL
            "port": _env.DJANGO_MAILER_PORT,
            "username": _env.DJANGO_MAILER_USERNAME,
            "password": _env.DJANGO_MAILER_PASSWORD.get_secret_value(),
            "use_tls": _env.DJANGO_MAILER_USE_TLS,
            "use_ssl": _env.DJANGO_MAILER_USE_SSL,
            # Seconds per SMTP operation; the request waits this long if the provider hangs.
            "timeout": _env.DJANGO_MAILER_TIMEOUT,
        },
    },
}
# Error emails must never raise while Django is already handling a 500. With
# MAILERS defined, AdminEmailHandler leaves fail_silently to the mailer it uses.
MAILERS["admin-logging"] = {
    "BACKEND": MAILERS["default"]["BACKEND"],
    "OPTIONS": {**MAILERS["default"]["OPTIONS"], "fail_silently": True},
}

ADMIN_URL = _env.DJANGO_ADMIN_URL


# Optional: an empty SENTRY_DSN disables Sentry (see SENTRY below). Read here
# because LOGGING depends on it.
SENTRY_DSN = _env.SENTRY_DSN.get_secret_value()

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "require_debug_false": {"()": "django.utils.log.RequireDebugFalse"},
        "request_id": {"()": "core.logging.RequestIdFilter"},
    },
    "formatters": LOG_FORMATTERS,
    "handlers": {
        "mail_admins": {
            "level": "ERROR",
            "filters": ["require_debug_false"],
            "class": "django.utils.log.AdminEmailHandler",
            "using": "admin-logging",
        },
        "console": {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
            "filters": ["request_id"],
            # JSON by default in production; DJANGO_LOG_FORMAT=text for plain lines.
            "formatter": _env.DJANGO_LOG_FORMAT,
        },
    },
    "root": {"level": "INFO", "handlers": ["console"]},
    "loggers": {
        # Without them the worker's task lines would be plain text (base.py).
        **LOGGERS_FOR_CELERY,
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "django.request": {
            # Error emails only when someone receives them and Sentry does not
            # already report the error (it would send every alert twice).
            "handlers": ["console", "mail_admins"]
            if ADMINS and not SENTRY_DSN
            else ["console"],
            "level": "ERROR",
            "propagate": False,
        },
        "django.security.DisallowedHost": {
            "level": "ERROR",
            "handlers": ["console"],
            "propagate": False,
        },
    },
}

# Tools that generate code samples can use SERVERS to point to the correct domain
NINJA_SERVERS = [
    {"url": f"https://{host.lstrip('.')}", "description": "Production server"}
    for host in ALLOWED_HOSTS
    if host != "*"
]

# Optional: an empty SENTRY_DSN (read above, before LOGGING) disables it.
# See docs/security.md#error-tracking.
if SENTRY_DSN:
    from core.sentry import init_sentry

    init_sentry(
        dsn=SENTRY_DSN,
        environment=_env.SENTRY_ENVIRONMENT,
        release=APP_RELEASE,
        traces_sample_rate=_env.SENTRY_TRACES_SAMPLE_RATE,
    )

# Keep last: refuse example or weak values. See docs/security.md#production-secrets.
# A DATABASE_URL without a password (peer or trust auth) parses to "": pass None
# so only an empty POSTGRES_PASSWORD counts as a value left blank by mistake.
_db_password = DATABASES["default"].get("PASSWORD")
if not _db_password and _env.DATABASE_URL is not None:
    _db_password = None
assert_production_secrets(
    secret_key=SECRET_KEY,
    secret_key_fallbacks=SECRET_KEY_FALLBACKS,
    admin_url=ADMIN_URL,
    db_password=_db_password,
)
assert_production_hosts(
    allowed_hosts=ALLOWED_HOSTS,
    csrf_trusted_origins=CSRF_TRUSTED_ORIGINS,
    cors_allowed_origins=CORS_ALLOWED_ORIGINS,
)
assert_production_email(
    mailers=MAILERS,
    default_from_email=DEFAULT_FROM_EMAIL,
    frontend_url=FRONTEND_URL,
)
assert_production_valkey(
    url=VALKEY_URL,
    password=VALKEY_PASSWORD,
    cache_db=VALKEY_CACHE_DB,
    broker_db=VALKEY_BROKER_DB,
)
assert_production_celery(
    # Not a setting anywhere but in test.py: read it from whatever this module
    # ended up with, so a later mistake cannot switch it on unnoticed.
    always_eager=bool(globals().get("CELERY_TASK_ALWAYS_EAGER", False)),
    broker_url=CELERY_BROKER_URL,
)
assert_production_hsts(
    seconds=SECURE_HSTS_SECONDS,
    include_subdomains=SECURE_HSTS_INCLUDE_SUBDOMAINS,
    preload=SECURE_HSTS_PRELOAD,
)
