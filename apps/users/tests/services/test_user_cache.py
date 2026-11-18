"""Every service that writes a user invalidates the cached users, on commit."""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING

import pytest
from django.db import transaction

from apps.users.selectors import USER_NAMESPACE
from apps.users.services import user_deactivate
from apps.users.services import user_reactivate
from apps.users.services import user_update
from core.cache import build_key

if TYPE_CHECKING:
    from collections.abc import Callable

    from apps.users.models import User
    from conftest import CacheWrites

pytestmark = pytest.mark.django_db

SERVICES: dict[str, Callable[[User], object]] = {
    "user_update": lambda user: user_update(user=user, name="Ada Lovelace"),
    "user_deactivate": lambda user: user_deactivate(user=user),
    "user_reactivate": lambda user: user_reactivate(user=user),
}


class _RollbackError(Exception):
    pass


def _current_version() -> str:
    # The key of a fixed part changes exactly when the namespace version does.
    return build_key(USER_NAMESPACE, "probe")


@pytest.mark.parametrize("service", SERVICES.values(), ids=SERVICES.keys())
def test_the_service_invalidates_cached_users_after_the_commit(
    user: User,
    cache_writes: CacheWrites,
    service: Callable[[User], object],
) -> None:
    before = _current_version()

    with cache_writes():
        service(user)
        # Still inside the transaction: nothing is invalidated yet.
        assert _current_version() == before

    assert _current_version() != before


@pytest.mark.parametrize("service", SERVICES.values(), ids=SERVICES.keys())
def test_a_rolled_back_service_invalidates_nothing(
    user: User,
    cache_writes: CacheWrites,
    service: Callable[[User], object],
) -> None:
    before = _current_version()

    with (
        cache_writes() as callbacks,
        suppress(_RollbackError),
        transaction.atomic(),
    ):
        service(user)
        raise _RollbackError

    assert callbacks == []
    assert _current_version() == before
