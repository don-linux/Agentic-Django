"""
The Valkey cache backend: a TTL on every key, and a policy for outages.

See docs/security.md#valkey-outages.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING
from typing import Any

from django.core.cache.backends.base import DEFAULT_TIMEOUT
from django.core.cache.backends.redis import RedisCache
from redis.exceptions import ConnectionError as ValkeyConnectionError
from redis.exceptions import TimeoutError as ValkeyTimeoutError

if TYPE_CHECKING:
    from collections.abc import Callable

# The logger of core.cache: one name for everything the cache layer reports.
logger = logging.getLogger("core.cache")

# Used when the CACHES entry has no MAX_TTL: 30 days, in seconds.
DEFAULT_MAX_TTL = 2_592_000
# Unreachable, timed out, or refusing the password (a ConnectionError subclass).
OUTAGE_ERRORS = (ValkeyConnectionError, ValkeyTimeoutError)
# Seconds between two outage warnings of one process: every request fails several
# cache calls during an outage, and one line per call would flood the log.
WARNING_INTERVAL = 30.0
# allauth and django-axes delete these rate-limit keys on every login: those
# deletes fail open, or each login would be a 500 during an outage.
# See docs/security.md#valkey-outages.
FAIL_OPEN_DELETE_PREFIXES = ("allauth:rl:", "axes-")
# The key `ping()` touches. Nothing writes it, and EXPIRE on a missing key
# changes nothing. See docs/ARCHITECTURE.md#deployment.
PING_KEY = "health:ping"


class OutageLog:
    """Log a Valkey outage at most once every `interval` seconds per process."""

    def __init__(
        self,
        interval: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.interval = interval
        self._clock = clock
        self._lock = threading.Lock()
        self._last = float("-inf")

    def warn(self, operation: str, error: Exception) -> None:
        now = self._clock()
        with self._lock:
            if now - self._last < self.interval:
                return
            self._last = now
        # The error text names the host and port, never the password.
        logger.warning(
            "Valkey is unreachable: cache %s failed open (%s: %s). Throttles and "
            "rate limits are not enforced until it is back.",
            operation,
            type(error).__name__,
            error,
        )


outage_log = OutageLog(WARNING_INTERVAL)


class ValkeyCache(RedisCache):
    """
    Django's `RedisCache` for Valkey: a TTL on every key, and an outage policy.

    `timeout=None` means `MAX_TTL`, a top-level CACHES key: `volatile-lru` only
    evicts keys that have a TTL. Reads and writes fail open; deletes raise, except
    under FAIL_OPEN_DELETE_PREFIXES; `ping()` and `strict_set()` never fail open.
    See docs/security.md#valkey-outages and docs/ARCHITECTURE.md#caching.
    """

    def __init__(self, server: Any, params: dict[str, Any]) -> None:
        super().__init__(server, params)
        self.max_ttl = int(params.get("MAX_TTL", DEFAULT_MAX_TTL))

    def get_backend_timeout(self, timeout: Any = DEFAULT_TIMEOUT) -> int:
        backend_timeout = super().get_backend_timeout(timeout)
        return self.max_ttl if backend_timeout is None else int(backend_timeout)

    def ping(self) -> None:
        """
        Raise unless Valkey answers on the write connection (wrong credentials too).

        Django's cache API has no ping: this is `RedisCache.touch()` on PING_KEY,
        one EXPIRE, bypassing the fail-open `touch()` below.
        """
        super().touch(PING_KEY)

    def strict_set(
        self,
        key: Any,
        value: Any,
        timeout: Any = DEFAULT_TIMEOUT,
        version: int | None = None,
    ) -> None:
        """Store a value, raising when Valkey is unreachable."""
        super().set(key, value, timeout, version)

    def get(self, key: Any, default: Any = None, version: int | None = None) -> Any:
        try:
            return super().get(key, default, version)
        except OUTAGE_ERRORS as error:
            outage_log.warn("get", error)
            return default

    def get_many(self, keys: Any, version: int | None = None) -> dict[Any, Any]:
        try:
            return super().get_many(keys, version)
        except OUTAGE_ERRORS as error:
            outage_log.warn("get_many", error)
            return {}

    def has_key(self, key: Any, version: int | None = None) -> bool:
        try:
            return super().has_key(key, version)
        except OUTAGE_ERRORS as error:
            outage_log.warn("has_key", error)
            return False

    def set(
        self,
        key: Any,
        value: Any,
        timeout: Any = DEFAULT_TIMEOUT,
        version: int | None = None,
    ) -> None:
        try:
            super().set(key, value, timeout, version)
        except OUTAGE_ERRORS as error:
            outage_log.warn("set", error)

    def set_many(
        self,
        data: Any,
        timeout: Any = DEFAULT_TIMEOUT,
        version: int | None = None,
    ) -> list[Any]:
        try:
            return super().set_many(data, timeout, version)
        except OUTAGE_ERRORS as error:
            outage_log.warn("set_many", error)
            # Django's contract: the keys that could not be stored.
            return list(data)

    def add(
        self,
        key: Any,
        value: Any,
        timeout: Any = DEFAULT_TIMEOUT,
        version: int | None = None,
    ) -> bool:
        try:
            return super().add(key, value, timeout, version)
        except OUTAGE_ERRORS as error:
            outage_log.warn("add", error)
            # As on an empty cache. False would make allauth's rate-limit lock
            # wait a second and then answer 429 to every login.
            return True

    def touch(
        self,
        key: Any,
        timeout: Any = DEFAULT_TIMEOUT,
        version: int | None = None,
    ) -> bool:
        try:
            return super().touch(key, timeout, version)
        except OUTAGE_ERRORS as error:
            outage_log.warn("touch", error)
            return False

    def incr(self, key: Any, delta: int = 1, version: int | None = None) -> int:
        try:
            return super().incr(key, delta, version)
        except OUTAGE_ERRORS as error:
            outage_log.warn("incr", error)
            # The documented answer for a key that does not exist.
            message = f"Key {key!r} not found."
            raise ValueError(message) from error

    def delete(self, key: Any, version: int | None = None) -> bool:
        try:
            return super().delete(key, version)
        except OUTAGE_ERRORS as error:
            if not str(key).startswith(FAIL_OPEN_DELETE_PREFIXES):
                raise
            outage_log.warn("delete", error)
            return False
