"""
The environment variables each settings module reads, with their types.

One class per module: base.py reads BaseEnv, and local.py, test.py and
production.py read LocalEnv, TestEnv and ProductionEnv. A class lists what its
own module reads, so a variable two modules read with different rules appears in
both (DJANGO_DEBUG defaults to True only in local.py; VALKEY_URL is required only
in production.py). A field is named exactly like its variable. read_env() turns a
missing or invalid value into ImproperlyConfigured that names the variable and
never prints its value. See docs/settings.md#environment-variables.
"""

from pathlib import Path
from typing import Annotated
from typing import Literal

from django.core.exceptions import ImproperlyConfigured
from pydantic import BeforeValidator
from pydantic import SecretStr
from pydantic import ValidationError
from pydantic_settings import BaseSettings
from pydantic_settings import NoDecode
from pydantic_settings import SettingsConfigDict

BASE_DIR = Path(__file__).resolve(strict=True).parent.parent.parent


def split_commas(value: object) -> object:
    """Split a comma-separated value; items are stripped and empty ones dropped."""
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


def empty_as_none(value: object) -> object:
    """Read an empty value as unset."""
    return None if value == "" else value


# Comma-separated, not the JSON list pydantic-settings reads by default.
CommaList = Annotated[list[str], NoDecode, BeforeValidator(split_commas)]
CommaSecrets = Annotated[list[SecretStr], NoDecode, BeforeValidator(split_commas)]
# Unset and empty both mean "no value".
OptionalStr = Annotated[str | None, BeforeValidator(empty_as_none)]
OptionalInt = Annotated[int | None, BeforeValidator(empty_as_none)]
OptionalSecret = Annotated[SecretStr | None, BeforeValidator(empty_as_none)]


class EnvironmentVariables(BaseSettings):
    """Read from the process environment only, by exact, case-sensitive name."""

    # A value that fails validation is never printed. No .env file and no secrets
    # directory: local.py loads the root .env into the environment itself.
    model_config = SettingsConfigDict(case_sensitive=True, hide_input_in_errors=True)


class BaseEnv(EnvironmentVariables):
    """The variables config/settings/base.py reads, in every environment."""

    DJANGO_DEBUG: bool = False
    APP_RELEASE: str = "dev"
    DJANGO_TIME_ZONE: str = "UTC"
    # Set and not empty, it replaces the POSTGRES_* connection values.
    DATABASE_URL: OptionalSecret = None
    # Required when DATABASE_URL is unset; base.py checks them.
    POSTGRES_DB: str | None = None
    POSTGRES_USER: str | None = None
    POSTGRES_PASSWORD: SecretStr | None = None
    POSTGRES_HOST: str = "postgres_db"
    POSTGRES_PORT: str = "5432"
    POSTGRES_CONNECT_TIMEOUT: int = 5
    VALKEY_URL: str = "redis://localhost:6379"
    VALKEY_USERNAME: str = ""
    VALKEY_PASSWORD: SecretStr = SecretStr("")
    VALKEY_CACHE_DB: int = 0
    VALKEY_BROKER_DB: int = 1
    VALKEY_CONNECT_TIMEOUT: float = 1.0
    VALKEY_SOCKET_TIMEOUT: float = 1.0
    VALKEY_MAX_TTL: int = 2_592_000
    DJANGO_CACHE_KEY_PREFIX: str = ""
    DJANGO_CACHE_VERSION: int = 1
    DJANGO_CACHE_TIMEOUT: int = 300
    # A full broker URL, which may hold a password; empty builds it from VALKEY_*.
    CELERY_BROKER_URL: SecretStr = SecretStr("")
    CELERY_TASK_SOFT_TIME_LIMIT: int = 1500
    CELERY_TASK_TIME_LIMIT: int = 1800
    CELERY_WORKER_CONCURRENCY: int = 2
    CELERY_WORKER_MAX_TASKS_PER_CHILD: int = 100
    CELERY_WORKER_MAX_MEMORY_PER_CHILD: int = 200_000
    DJANGO_ARGON2_TIME_COST: int = 2
    DJANGO_ARGON2_MEMORY_COST_KIB: int = 102_400
    DJANGO_ARGON2_PARALLELISM: int = 8
    DJANGO_MEDIA_ROOT: str = str(BASE_DIR / "media")
    DJANGO_MEDIA_URL: str = "/media/"
    DJANGO_PRIVATE_MEDIA_ROOT: str = str(BASE_DIR / "private_media")
    DJANGO_PRIVATE_MEDIA_ACCEL_REDIRECT_PREFIX: str = ""
    DJANGO_ADMINS: CommaList = []
    DJANGO_LOG_FORMAT: Literal["text", "json"] = "text"
    AXES_FAILURE_LIMIT: int = 5
    DJANGO_DEFAULT_FROM_EMAIL: str = "Project <noreply@example.com>"
    # Unset: the value of DJANGO_DEFAULT_FROM_EMAIL.
    DJANGO_SERVER_EMAIL: str | None = None
    DJANGO_EMAIL_SUBJECT_PREFIX: str = "[Project] "
    DJANGO_EMAIL_SIGNATURE_NAME: str = "Project"
    DJANGO_ACCOUNT_ALLOW_REGISTRATION: bool = True
    FRONTEND_URL: str = "http://localhost:3000"
    DJANGO_THROTTLE_ANON_RATE: str = "60/min"
    DJANGO_THROTTLE_USER_RATE: str = "600/min"
    DJANGO_API_MAX_PAGE_NUMBER: int = 1000
    CORS_ALLOWED_ORIGINS: CommaList = []


class LocalEnv(EnvironmentVariables):
    """The variables config/settings/local.py reads, with development defaults."""

    DJANGO_DEBUG: bool = True
    # Unset: local.py's insecure development key.
    DJANGO_SECRET_KEY: SecretStr | None = None
    DJANGO_MAILER_HOST: str = "mailpit"
    DJANGO_MAILER_PORT: int = 1025
    DJANGO_LOG_SQL: bool = False
    # Only an explicit broker URL; the one base.py builds from VALKEY_URL is
    # already checked.
    CELERY_BROKER_URL: SecretStr = SecretStr("")
    DJANGO_ALLOW_REMOTE_LOCAL: bool = False


class TestEnv(EnvironmentVariables):
    """The variables config/settings/test.py reads."""

    # pytest would collect a class named Test* that a test module imports.
    __test__ = False

    # Unset: test.py's insecure key.
    DJANGO_SECRET_KEY: SecretStr | None = None


class ProductionEnv(EnvironmentVariables):
    """The variables config/settings/production.py reads; no default: required."""

    DJANGO_LOG_FORMAT: Literal["text", "json"] = "json"
    FRONTEND_URL: str = "https://app.example.com"
    # Set and not empty: the database password may be empty (peer or trust auth).
    DATABASE_URL: OptionalSecret = None
    DJANGO_SECRET_KEY: SecretStr
    # Old keys still accepted for sessions and signed tokens while rotating.
    DJANGO_SECRET_KEY_FALLBACKS: CommaSecrets = []
    DJANGO_ALLOWED_HOSTS: CommaList
    CONN_MAX_AGE: int = 60
    CONN_HEALTH_CHECKS: bool = True
    POSTGRES_STATEMENT_TIMEOUT_MS: int = 30_000
    POSTGRES_LOCK_TIMEOUT_MS: int = 0
    # Required here: production must never fall back to base.py's localhost URL
    # or empty password.
    VALKEY_URL: str
    VALKEY_PASSWORD: SecretStr
    # Proxies that append to X-Forwarded-For (nginx alone: 1).
    DJANGO_TRUSTED_PROXY_COUNT: int = 1
    DJANGO_SECURE_SSL_REDIRECT: bool = True
    DJANGO_CSRF_TRUSTED_ORIGINS: CommaList = []
    DJANGO_SECURE_HSTS_SECONDS: int = 60
    DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS: bool = False
    DJANGO_SECURE_HSTS_PRELOAD: bool = False
    DJANGO_SECURE_CONTENT_TYPE_NOSNIFF: bool = True
    DJANGO_STORAGE_BACKEND: Literal["local", "s3"] = "local"
    # Required with DJANGO_STORAGE_BACKEND=s3; production.py checks it.
    DJANGO_AWS_STORAGE_BUCKET_NAME: str | None = None
    DJANGO_AWS_S3_REGION_NAME: OptionalStr = None
    DJANGO_AWS_S3_ENDPOINT_URL: OptionalStr = None
    DJANGO_AWS_S3_CUSTOM_DOMAIN: OptionalStr = None
    DJANGO_AWS_QUERYSTRING_EXPIRE: int = 300
    DJANGO_MAILER_HOST: str
    # Unset: the standard port for the protocol, 587 with TLS and 465 with SSL.
    DJANGO_MAILER_PORT: OptionalInt = None
    DJANGO_MAILER_USERNAME: str = ""
    DJANGO_MAILER_PASSWORD: SecretStr = SecretStr("")
    DJANGO_MAILER_USE_TLS: bool = True
    DJANGO_MAILER_USE_SSL: bool = False
    DJANGO_MAILER_TIMEOUT: int = 10
    DJANGO_ADMIN_URL: str
    # Empty: Sentry is off.
    SENTRY_DSN: SecretStr = SecretStr("")
    SENTRY_ENVIRONMENT: str = "production"
    SENTRY_TRACES_SAMPLE_RATE: float = 0.0


def read_env[E: EnvironmentVariables](env_class: type[E]) -> E:
    """
    Read `env_class` from the process environment.

    Every missing or invalid variable is reported at once, by name. The values
    never appear in the message, which may reach a log or a terminal.
    """
    try:
        return env_class()
    except ValidationError as error:
        problems = [
            f"Set the {error_detail['loc'][0]} environment variable."
            if error_detail["type"] == "missing"
            else f"{error_detail['loc'][0]}: {error_detail['msg']}."
            for error_detail in error.errors(include_url=False, include_input=False)
        ]
        raise ImproperlyConfigured(" ".join(problems)) from None


def required[T](value: T | None, name: str) -> T:
    """Return `value`, refusing None: a variable only some settings require."""
    if value is None:
        msg = f"Set the {name} environment variable."
        raise ImproperlyConfigured(msg)
    return value
