from __future__ import annotations

import pickle
import uuid
from contextlib import suppress
from typing import TYPE_CHECKING

import pytest
from django.db import transaction

from apps.users.models import User
from apps.users.selectors import USER_CACHE_VERSION
from apps.users.selectors import user_get
from apps.users.selectors import user_list
from apps.users.services import user_update
from apps.users.tests.factories import UserFactory
from core.tests.credentials import PASSWORD

if TYPE_CHECKING:
    from conftest import CacheWrites

pytestmark = pytest.mark.django_db


class _RollbackError(Exception):
    pass


def test_user_get(user: User) -> None:
    assert user_get(public_id=user.public_id) == user


def test_user_get_missing_raises_does_not_exist() -> None:
    with pytest.raises(User.DoesNotExist):
        user_get(public_id=uuid.uuid7())


def test_user_get_is_served_from_the_cache_the_second_time(
    user: User,
    cache_writes: CacheWrites,
    django_assert_num_queries,
) -> None:
    with cache_writes(), django_assert_num_queries(1):
        first = user_get(public_id=user.public_id)

    with django_assert_num_queries(0):
        second = user_get(public_id=user.public_id)

    assert first == second == user


def test_a_cached_user_is_never_returned_for_another_id(
    cache_writes: CacheWrites,
    django_assert_num_queries,
) -> None:
    first = UserFactory(name="Ada")
    second = UserFactory(name="Grace")
    with cache_writes():
        user_get(public_id=first.public_id)

    with django_assert_num_queries(1):
        got = user_get(public_id=second.public_id)

    assert got == second
    assert got.name == "Grace"


def test_a_new_cache_version_ignores_the_entries_of_the_old_one(
    user: User,
    cache_writes: CacheWrites,
    django_assert_num_queries,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The version goes up when User's fields change: the instances pickled
    # under the old one must not come back.
    with cache_writes():
        user_get(public_id=user.public_id)
    monkeypatch.setattr(
        "apps.users.selectors.USER_CACHE_VERSION", USER_CACHE_VERSION + 1
    )

    with django_assert_num_queries(1):
        assert user_get(public_id=user.public_id) == user


def test_user_get_returns_fresh_data_after_a_write_through_the_service(
    user: User,
    cache_writes: CacheWrites,
    django_assert_num_queries,
) -> None:
    with cache_writes():
        user_get(public_id=user.public_id)
    with cache_writes():
        user_update(user=user, name="Ada Lovelace")

    with django_assert_num_queries(1):
        fresh = user_get(public_id=user.public_id)

    assert fresh.name == "Ada Lovelace"


def test_a_rolled_back_write_leaves_the_cached_user_untouched(
    user: User,
    cache_writes: CacheWrites,
    django_assert_num_queries,
) -> None:
    saved_name = user.name
    with cache_writes():
        user_get(public_id=user.public_id)

    with cache_writes(), suppress(_RollbackError), transaction.atomic():
        user_update(user=user, name="Never saved")
        raise _RollbackError

    with django_assert_num_queries(0):
        cached = user_get(public_id=user.public_id)

    assert cached.name == saved_name


def test_the_cached_user_never_holds_the_password_hash(
    cache_writes: CacheWrites,
) -> None:
    user = UserFactory(password=PASSWORD)
    with cache_writes():
        user_get(public_id=user.public_id)

    cached = user_get(public_id=user.public_id)

    assert "password" in cached.get_deferred_fields()
    assert user.password.encode() not in pickle.dumps(cached)
    # The field still loads when something needs it.
    assert cached.check_password(PASSWORD)


def test_a_missing_user_is_not_cached(
    cache_writes: CacheWrites,
) -> None:
    public_id = uuid.uuid7()
    with cache_writes(), pytest.raises(User.DoesNotExist):
        user_get(public_id=public_id)
    created = UserFactory(public_id=public_id)

    assert user_get(public_id=public_id) == created


def test_user_list_returns_only_the_requester(user: User) -> None:
    UserFactory.create_batch(2)

    assert list(user_list(user=user)) == [user]
    assert user_list(user=user).query.order_by == ("public_id",)
