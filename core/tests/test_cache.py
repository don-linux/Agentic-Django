from __future__ import annotations

import logging
import re
from contextlib import suppress
from typing import TYPE_CHECKING
from typing import Any
from unittest import mock

import pytest
from django.core.cache import caches
from django.db import transaction

from core.cache import _MAX_PARTS_LENGTH
from core.cache import NAMESPACE_TTL
from core.cache import build_key
from core.cache import invalidate
from core.cache import read_through
from core.cache_backend import ValkeyCache

if TYPE_CHECKING:
    from pytest_django.fixtures import DjangoCaptureOnCommitCallbacks
    from pytest_django.fixtures import Settings

pytestmark = pytest.mark.django_db

NAMESPACE = "tests.thing"
VERSION_KEY = f"ns:{NAMESPACE}"
ONE_MINUTE = 60
NAMESPACE_SHAPE = re.escape("<app>.<resource>")
# Valkey on a closed port: every call to it fails at once, as during an outage.
DEAD_VALKEY = {
    "default": {
        "BACKEND": "core.cache_backend.ValkeyCache",
        "LOCATION": "redis://127.0.0.1:1/15",
        "OPTIONS": {"socket_connect_timeout": 0.05, "socket_timeout": 0.05},
    },
}


class _RollbackError(Exception):
    pass


class _Loader:
    def __init__(self, value: Any) -> None:
        self.value = value
        self.calls = 0

    def __call__(self) -> Any:
        self.calls += 1
        return self.value


def _read(loader: _Loader, *parts: object) -> Any:
    return read_through(NAMESPACE, *parts, ttl=ONE_MINUTE, loader=loader)


def test_build_key_has_the_namespace_its_version_and_the_parts() -> None:
    key = build_key(NAMESPACE, 42, "es")

    assert re.fullmatch(r"tests\.thing:v\d+:42:es", key)
    # The version is stored: the same parts give the same key.
    assert build_key(NAMESPACE, 42, "es") == key


@pytest.mark.parametrize(
    "part",
    ["two words", "a:b", "ñandú", "x" * 65, "", "line\nbreak"],
)
def test_unsafe_parts_are_hashed(part: str) -> None:
    key = build_key(NAMESPACE, part)

    assert re.fullmatch(r"tests\.thing:v\d+:[0-9a-f]{32}", key)
    # No CacheKeyWarning (an error under filterwarnings=error) when it is used.
    caches["default"].set(key, 1, ONE_MINUTE)


def test_a_separator_in_a_part_cannot_forge_another_key() -> None:
    assert build_key(NAMESPACE, "a:b", "c") != build_key(NAMESPACE, "a", "b:c")


def test_many_parts_never_make_an_overlong_key() -> None:
    key = build_key(NAMESPACE, *["part-with-sixty-four-characters-" * 2] * 6)

    assert re.fullmatch(r"tests\.thing:v\d+:[0-9a-f]{32}", key)
    caches["default"].set(key, 1, ONE_MINUTE)


def test_a_tail_of_exactly_the_maximum_length_is_kept_as_it_is() -> None:
    # Each part is safe on its own (64 characters at most); only a longer
    # joined tail is hashed.
    parts = ("a" * 64, "b" * 64, "c" * (_MAX_PARTS_LENGTH - 130))
    tail = ":".join(parts)
    assert len(tail) == _MAX_PARTS_LENGTH

    assert build_key(NAMESPACE, *parts).endswith(f":{tail}")


@pytest.mark.parametrize("namespace", ["users", "Users.User", "users.user.x", "a b.c"])
def test_a_namespace_is_app_dot_resource(namespace: str) -> None:
    with pytest.raises(ValueError, match=NAMESPACE_SHAPE):
        build_key(namespace, 1)
    with pytest.raises(ValueError, match=NAMESPACE_SHAPE):
        invalidate(namespace)
    with pytest.raises(ValueError, match=NAMESPACE_SHAPE):
        read_through(namespace, 1, ttl=ONE_MINUTE, loader=_Loader(1))


@pytest.mark.parametrize("ttl", [0, -1])
def test_a_cached_read_needs_a_positive_ttl(ttl: int) -> None:
    with pytest.raises(ValueError, match="positive ttl"):
        read_through(NAMESPACE, 1, ttl=ttl, loader=_Loader(1))


def test_a_one_second_ttl_is_cached(
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    loader = _Loader("value")
    with django_capture_on_commit_callbacks(execute=True):
        read_through(NAMESPACE, 1, ttl=1, loader=loader)

    assert read_through(NAMESPACE, 1, ttl=1, loader=loader) == "value"
    assert loader.calls == 1


def test_a_cached_read_is_stored_with_its_ttl(
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    backend = caches["default"]
    timeouts: list[float | None] = []
    real_set = backend.set

    def set_(key: str, value: object, timeout: float | None = None) -> None:
        timeouts.append(timeout)
        real_set(key, value, timeout)

    monkeypatch.setattr(backend, "set", set_)
    with django_capture_on_commit_callbacks(execute=True):
        _read(_Loader("value"), 1)

    assert timeouts == [ONE_MINUTE]


def test_a_read_is_stored_only_after_the_commit() -> None:
    loader = _Loader("value")

    # A test runs inside a transaction that never commits: every read is a miss.
    assert _read(loader, 1) == "value"
    assert _read(loader, 1) == "value"

    assert loader.calls == 2  # noqa: PLR2004


def test_the_second_read_is_a_hit_once_the_first_was_committed(
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    loader = _Loader({"id": 1})

    with django_capture_on_commit_callbacks(execute=True):
        _read(loader, 1)

    assert _read(loader, 1) == {"id": 1}
    assert loader.calls == 1
    assert _read(loader, 2) == {"id": 1}
    assert loader.calls == 2  # noqa: PLR2004


def test_none_is_cached_too(
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    loader = _Loader(None)

    with django_capture_on_commit_callbacks(execute=True):
        _read(loader, 1)

    assert _read(loader, 1) is None
    assert loader.calls == 1


def test_a_read_inside_a_rolled_back_transaction_stores_nothing(
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    loader = _Loader("uncommitted")

    with (
        django_capture_on_commit_callbacks(execute=True) as callbacks,
        suppress(_RollbackError),
        transaction.atomic(),
    ):
        _read(loader, 1)
        raise _RollbackError

    assert callbacks == []
    _read(loader, 1)
    assert loader.calls == 2  # noqa: PLR2004


def test_a_failing_cache_read_falls_back_to_the_loader(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken_get(*_args: object, **_kwargs: object) -> None:
        message = "this value no longer unpickles"
        raise RuntimeError(message)

    monkeypatch.setattr(caches["default"], "get", broken_get)

    with caplog.at_level(logging.WARNING, logger="core.cache"):
        value = _read(_Loader("from the database"), 1)

    assert value == "from the database"
    assert [record.getMessage() for record in caplog.records] == [
        f"cache read failed for {NAMESPACE}",
    ]
    # The warning carries the traceback, not a bare True or False.
    exc_info = caplog.records[0].exc_info
    assert exc_info
    assert exc_info[0] is RuntimeError


def test_a_failing_store_after_the_commit_does_not_fail_the_request(
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    def broken_set(*_args: object, **_kwargs: object) -> None:
        message = "cannot pickle this value"
        raise RuntimeError(message)

    monkeypatch.setattr(caches["default"], "set", broken_set)

    with django_capture_on_commit_callbacks(execute=True):
        value = _read(_Loader("from the database"), 1)

    assert value == "from the database"


def test_reads_still_answer_during_a_valkey_outage(settings: Settings) -> None:
    settings.CACHES = DEAD_VALKEY
    loader = _Loader("from the database")

    assert _read(loader, 1) == "from the database"
    assert _read(loader, 1) == "from the database"
    assert loader.calls == 2  # noqa: PLR2004


def test_invalidate_drops_every_key_of_the_namespace_after_the_commit(
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    loader = _Loader("value")
    with django_capture_on_commit_callbacks(execute=True):
        _read(loader, 1)
        _read(loader, 2)
    before = caches["default"].get(VERSION_KEY)

    with django_capture_on_commit_callbacks(execute=True):
        invalidate(NAMESPACE)

    assert caches["default"].get(VERSION_KEY) != before
    _read(loader, 1)
    _read(loader, 2)
    assert loader.calls == 4  # noqa: PLR2004


def test_a_rolled_back_write_invalidates_nothing(
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    before = build_key(NAMESPACE, 1)

    with (
        django_capture_on_commit_callbacks(execute=True) as callbacks,
        suppress(_RollbackError),
        transaction.atomic(),
    ):
        invalidate(NAMESPACE)
        raise _RollbackError

    assert callbacks == []
    assert build_key(NAMESPACE, 1) == before


def test_namespace_versions_expire_like_every_other_key(
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    backend = caches["default"]
    timeouts: list[float | None] = []
    real_add, real_set = backend.add, backend.set

    def add(key: str, value: object, timeout: float | None = None) -> bool:
        timeouts.append(timeout)
        return real_add(key, value, timeout)

    def set_(key: str, value: object, timeout: float | None = None) -> None:
        timeouts.append(timeout)
        real_set(key, value, timeout)

    monkeypatch.setattr(backend, "add", add)
    monkeypatch.setattr(backend, "set", set_)

    build_key(NAMESPACE, 1)
    with django_capture_on_commit_callbacks(execute=True):
        invalidate(NAMESPACE)

    # Valkey only evicts keys that have a TTL: the version is no exception.
    assert timeouts == [NAMESPACE_TTL, NAMESPACE_TTL]


def test_invalidate_on_valkey_writes_a_new_version_that_expires(
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    backend = mock.Mock(spec=ValkeyCache)
    monkeypatch.setattr("core.cache._backend", lambda: backend)

    with django_capture_on_commit_callbacks(execute=True):
        invalidate(NAMESPACE)
        invalidate(NAMESPACE)

    first, second = (call.args for call in backend.strict_set.call_args_list)
    assert None not in (first[1], second[1])
    assert first[1] != second[1]
    assert first[2] == second[2] == NAMESPACE_TTL


def test_a_lost_version_never_revives_old_entries(
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        _read(_Loader("old"), 1)
    # The version key expires or is evicted, while the entry is still there.
    caches["default"].delete(VERSION_KEY)

    fresh = _Loader("fresh")

    assert _read(fresh, 1) == "fresh"
    assert fresh.calls == 1


def test_a_version_lost_right_after_it_was_added_still_gives_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Evicted between add() and the read back: the key uses the version this
    # call wrote.
    backend = caches["default"]
    monkeypatch.setattr(backend, "add", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(backend, "get", lambda _key, default=None: default)

    assert re.fullmatch(r"tests\.thing:v\d+:1", build_key(NAMESPACE, 1))


def test_invalidate_never_fails_open(
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    settings.CACHES = DEAD_VALKEY

    # The data is already committed, so the request must not fail; but an
    # invalidation that did not happen has to be an error in the log, not a
    # silent fail-open write.
    with (
        caplog.at_level(logging.ERROR),
        django_capture_on_commit_callbacks(execute=True),
    ):
        invalidate(NAMESPACE)

    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "_replace_version" in errors[0].getMessage()
    assert NAMESPACE in errors[0].getMessage()
