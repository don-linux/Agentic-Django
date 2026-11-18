"""
Test settings: in-memory cache and broker, inline tasks.

See docs/settings.md#test.
"""

from typing import cast

from .base import *  # noqa: F403
from .base import INSTALLED_APPS
from .base import MIDDLEWARE
from .base import REST_FRAMEWORK
from .base import build_headless_frontend_urls
from .base import env

DEBUG = False

# Dev Compose injects the root .env into the containers, so the shell running the
# tests may hold any project value. The tests check the template's defaults; only
# the database and Valkey connection come from the environment.
# See docs/settings.md#test.
TIME_ZONE = "UTC"
CELERY_TIMEZONE = TIME_ZONE
FRONTEND_URL = "http://localhost:3000"
HEADLESS_FRONTEND_URLS = build_headless_frontend_urls(FRONTEND_URL)
CORS_ALLOWED_ORIGINS = []
DEFAULT_FROM_EMAIL = "Project <noreply@example.com>"
SERVER_EMAIL = DEFAULT_FROM_EMAIL
EMAIL_SUBJECT_PREFIX = "[Project] "
ACCOUNT_EMAIL_SUBJECT_PREFIX = EMAIL_SUBJECT_PREFIX
EMAIL_SIGNATURE_NAME = "Project"
MFA_TOTP_ISSUER = EMAIL_SIGNATURE_NAME
ACCOUNT_ALLOW_REGISTRATION = True
REST_FRAMEWORK_MAX_PAGE_NUMBER = 1000
SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    # Development/test only. Production requires DJANGO_SECRET_KEY.
    default="django-insecure-otKr9mZP2VQewcgyiVcGv5qzNzkYhqXEz5_78Z_6grdsa9KFJKOtrxdAdDT0GQaM",
)
# No scan of every static file at startup, which slows each test process.
WHITENOISE_AUTOREFRESH = True

# Tests never reach a real Valkey, except the valkey suite.
# See docs/settings.md#test.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "KEY_PREFIX": "test",
    },
}
# Tasks run inline and the broker is in memory; eager mode exists only here.
# See docs/settings.md#test.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = "memory://"

# axes.W001: LocMemCache is per process. See docs/settings.md#test.
SILENCED_SYSTEM_CHECKS = ["axes.W001"]

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}

# Throttling stays on: global rates out of reach, scoped rates kept.
# See docs/api.md#throttling.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {
        **cast("dict[str, str]", REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]),
        "anon": "10000/min",
        "user": "10000/min",
    },
}

MEDIA_URL = "http://media.testserver/"
# Any test request that runs an N+1 query fails.
# See docs/development.md#n-plus-one.
INSTALLED_APPS = [*INSTALLED_APPS, "zeal"]
MIDDLEWARE = [*MIDDLEWARE, "zeal.middleware.zeal_middleware"]
ZEAL_RAISE = True
ZEAL_NPLUSONE_THRESHOLD = 3
ZEAL_SHOW_ALL_CALLERS = True
ZEAL_ALLOWLIST = [{"model": "account.EmailAddress"}]
