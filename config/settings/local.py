# Imported first: it loads the root .env before base.py reads the environment.
from . import _env_file
from .base import *  # noqa: F403
from .base import DATABASES
from .base import INSTALLED_APPS
from .base import LOGGING
from .base import MIDDLEWARE
from .base import VALKEY_URL
from .env import LocalEnv
from .env import read_env
from .guards import assert_local_targets

# Every variable this module reads, typed (config/settings/_env.py).
_env = read_env(LocalEnv)
# The env file this process loaded; empty when DJANGO_ENV_FILE turned the read off.
ENV_FILE = _env_file.ENV_FILE
DEBUG = _env.DJANGO_DEBUG
SECRET_KEY = (
    # Development/test only. Production requires DJANGO_SECRET_KEY.
    "django-insecure-zoqf21oQqQcQBGuQ15G-TyeQ7W8wnJq7Rd9pI99hBrk91xRuF7IVvsCN3D88F5jl"
    if _env.DJANGO_SECRET_KEY is None
    else _env.DJANGO_SECRET_KEY.get_secret_value()
)
ALLOWED_HOSTS = ["localhost", "0.0.0.0", "127.0.0.1"]  # noqa: S104

# Mailpit catches every message: http://localhost:8025
MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "OPTIONS": {
            "host": _env.DJANGO_MAILER_HOST,
            "port": _env.DJANGO_MAILER_PORT,
            "timeout": 5,
        },
    },
}

INSTALLED_APPS = ["whitenoise.runserver_nostatic", *INSTALLED_APPS]


INSTALLED_APPS += ["django_extensions"]

# Same check as the tests, but it logs a warning instead of failing the request.
INSTALLED_APPS += ["zeal"]
MIDDLEWARE = [*MIDDLEWARE, "zeal.middleware.zeal_middleware"]
ZEAL_RAISE = False
ZEAL_NPLUSONE_THRESHOLD = 3
ZEAL_SHOW_ALL_CALLERS = True
ZEAL_ALLOWLIST = [{"model": "account.EmailAddress"}]

# DJANGO_LOG_SQL=True prints every query. See docs/development.md#sql-log.
if _env.DJANGO_LOG_SQL:
    LOGGING["loggers"] = {
        **LOGGING.get("loggers", {}),
        "django.db.backends": {
            "level": "DEBUG",
            "handlers": ["console"],
            "propagate": False,
        },
    }

# Refuse a remote DATABASE_URL or VALKEY_URL, from the shell or from the .env file.
assert_local_targets(
    database_host=str(DATABASES["default"].get("HOST") or ""),
    valkey_url=VALKEY_URL,
    # Only an explicit override: the URL built from VALKEY_URL is already checked.
    celery_broker_url=_env.CELERY_BROKER_URL.get_secret_value(),
    allow_remote=_env.DJANGO_ALLOW_REMOTE_LOCAL,
)
