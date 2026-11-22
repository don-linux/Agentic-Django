"""
Read-through caching for selectors, on the default cache (Valkey outside tests).

Keys are "<namespace>:v<version>:<parts>"; `invalidate()` replaces the namespace
version. See docs/ARCHITECTURE.md#caching.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from functools import partial
from typing import TYPE_CHECKING

from django.core.cache import DEFAULT_CACHE_ALIAS
from django.core.cache import caches
from django.db import transaction

from core.cache_backend import ValkeyCache

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.core.cache.backends.base import BaseCache

logger = logging.getLogger(__name__)

PROBE_KEY = "health:probe"
# Namespace versions expire like every other key: Valkey only evicts keys that
# have a TTL. Seven days, in seconds; an expired version is replaced by a new one.
NAMESPACE_TTL = 604_800
# A cached None has to be told apart from a miss.
_MISS = object()
_NAMESPACE = re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*")
# What a key part may contain as is. Anything else is hashed.
_SAFE_PART = re.compile(r"[A-Za-z0-9._-]{1,64}")
_MAX_PARTS_LENGTH = 160


def probe() -> None:
    """
    Raise unless the default cache answers; the readiness probe calls it.

    On Valkey this is `ValkeyCache.ping()`, which never fails open. `caches[...]`
    is the backend itself: `django.core.cache.cache` is a proxy, and no isinstance
    check matches it.
    """
    backend = _backend()
    if isinstance(backend, ValkeyCache):
        backend.ping()
    else:
        backend.get(PROBE_KEY)


def build_key(namespace: str, *parts: object) -> str:
    """
    Return the cache key for `parts` in the current version of `namespace`.

    Unsafe or long parts are hashed, so client input cannot forge a separator.
    """
    _check_namespace(namespace)
    tail = ":".join(_part(part) for part in parts)
    if len(tail) > _MAX_PARTS_LENGTH:
        tail = _digest(tail)
    return f"{namespace}:v{_namespace_version(namespace)}:{tail}"


def read_through[T](
    namespace: str,
    *parts: object,
    ttl: int,
    loader: Callable[[], T],
) -> T:
    """
    Return the cached value for `parts` in `namespace`, calling `loader` on a miss.

    `ttl` (seconds) has no default. The value is stored after the commit, and any
    cache error falls back to `loader`. See docs/ARCHITECTURE.md#caching.
    """
    if ttl <= 0:
        message = "A cached read needs a positive ttl, in seconds."
        raise ValueError(message)
    _check_namespace(namespace)
    try:
        key = build_key(namespace, *parts)
        cached = _backend().get(key, _MISS)
    except Exception:
        # Whatever the cache did wrong (a value that no longer unpickles, a server
        # error), the selector still answers from the database.
        logger.warning("cache read failed for %s", namespace, exc_info=True)
        return loader()
    if cached is not _MISS:
        return cached
    value = loader()

    def store() -> None:
        # A closure, not a partial: Django logs the callback's name when it
        # fails, and a partial's name would print the value.
        _backend().set(key, value, ttl)

    transaction.on_commit(store, robust=True)
    return value


def invalidate(namespace: str) -> None:
    """
    Drop every cached read of `namespace` once the current transaction commits.

    Never fails open: if Valkey is down after the commit, the error is logged and
    stale entries live until their TTL. See docs/ARCHITECTURE.md#caching.
    """
    _check_namespace(namespace)
    transaction.on_commit(partial(_replace_version, namespace), robust=True)


def _check_namespace(namespace: str) -> None:
    if not _NAMESPACE.fullmatch(namespace):
        message = f"A cache namespace is '<app>.<resource>', not {namespace!r}."
        raise ValueError(message)


def _backend() -> BaseCache:
    return caches[DEFAULT_CACHE_ALIAS]


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def _part(value: object) -> str:
    text = str(value)
    return text if _SAFE_PART.fullmatch(text) else _digest(text)


def _namespace_key(namespace: str) -> str:
    return f"ns:{namespace}"


def _namespace_version(namespace: str) -> int:
    backend = _backend()
    key = _namespace_key(namespace)
    version = backend.get(key)
    if version is None:
        # The clock, never a counter from 1: if this key expired or was evicted,
        # the new version can never match entries written under an older one.
        candidate = time.time_ns()
        backend.add(key, candidate, NAMESPACE_TTL)
        # Read it back: another process may have added its own first.
        version = backend.get(key, candidate)
    return int(version)


def _replace_version(namespace: str) -> None:
    backend = _backend()
    key = _namespace_key(namespace)
    version = time.time_ns()
    if isinstance(backend, ValkeyCache):
        backend.strict_set(key, version, NAMESPACE_TTL)
    else:
        backend.set(key, version, NAMESPACE_TTL)
