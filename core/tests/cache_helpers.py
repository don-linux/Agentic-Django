"""Valkey backend and login helpers shared by the cache and outage tests."""

from __future__ import annotations

from core.cache_backend import ValkeyCache
from core.tests.session_tokens import session_token

# A closed port: building the backend opens no connection, and every call to it
# fails at once, as when Valkey is down.
LOCATION = "redis://127.0.0.1:1/15"
MAX_TTL = 3600
ONE_MINUTE = 60
SESSION_URL = "/_allauth/app/v1/auth/session"
# The default cache of the outage tests: a closed port with short timeouts.
DEAD_LOCATION = "redis://127.0.0.1:1/7"
DEAD_OPTIONS = {"socket_connect_timeout": 0.05, "socket_timeout": 0.05}
DEAD_VALKEY = {
    "default": {
        "BACKEND": "core.cache_backend.ValkeyCache",
        "LOCATION": DEAD_LOCATION,
        "KEY_PREFIX": "dead-valkey",
        "OPTIONS": DEAD_OPTIONS,
    },
}


def _backend(**params: object) -> ValkeyCache:
    return ValkeyCache(LOCATION, {"KEY_PREFIX": "tests", **params})


def _login() -> str:
    return session_token("outage@example.com")


def _root_cause(error: BaseException, expected: type[BaseException]) -> bool:
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, expected):
            return True
        current = current.__cause__ or current.__context__
    return False
