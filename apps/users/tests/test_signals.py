"""Writes that skip the services still invalidate the cached users (signals.py)."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from allauth.account.models import EmailAddress
from django.db.models.deletion import Collector
from django.test import Client
from django.utils import timezone

from apps.users.models import User
from apps.users.selectors import USER_NAMESPACE
from apps.users.selectors import user_get
from apps.users.tests.factories import UserFactory
from core.cache import build_key
from core.cache import invalidate
from core.tests.credentials import PASSWORD

if TYPE_CHECKING:
    from conftest import CacheWrites

pytestmark = pytest.mark.django_db

LOGIN_URL = "/_allauth/app/v1/auth/login"


def _current_version() -> str:
    # The key of a fixed part changes exactly when the namespace version does.
    return build_key(USER_NAMESPACE, "probe")


def test_a_save_outside_the_services_invalidates(
    user: User,
    cache_writes: CacheWrites,
) -> None:
    before = _current_version()

    with cache_writes():
        user.name = "Changed by the admin"
        user.save()

    assert _current_version() != before


def test_a_delete_invalidates(cache_writes: CacheWrites) -> None:
    doomed = UserFactory()
    before = _current_version()

    # User.delete() is blocked; the hard_delete_user path deletes through a
    # Collector, which still sends post_delete.
    with cache_writes():
        collector = Collector(using="default")
        # django-stubs 6.1.2 types collect() as
        # _IndexableCollection[Model | None], a Protocol whose __getitem__
        # also takes `index` by name; typeshed's list and tuple take it
        # positional-only, so Pyright 1.1.414 does not treat list[User] or
        # tuple[User] as that protocol.
        collector.collect([doomed])  # pyright: ignore[reportArgumentType]
        collector.delete()

    assert _current_version() != before


def test_saving_only_last_login_does_not_invalidate(
    user: User,
    cache_writes: CacheWrites,
) -> None:
    before = _current_version()

    with cache_writes():
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])

    assert _current_version() == before


def test_a_login_does_not_empty_the_user_cache(cache_writes: CacheWrites) -> None:
    # Django saves last_login on every login: invalidating then would leave the
    # cache useless on any busy system.
    user = UserFactory(email="login@example.com", password=PASSWORD)
    EmailAddress.objects.create(
        user=user, email=user.email, primary=True, verified=True
    )
    before = _current_version()

    with cache_writes():
        response = Client().post(
            LOGIN_URL,
            {"email": user.email, "password": PASSWORD},
            content_type="application/json",
        )

    assert response.status_code == HTTPStatus.OK
    assert _current_version() == before


def test_a_queryset_update_needs_an_explicit_invalidate(
    user: User,
    cache_writes: CacheWrites,
) -> None:
    with cache_writes():
        user_get(public_id=user.public_id)

    # update() sends no signal: the cached copy is stale until its TTL...
    with cache_writes():
        User.objects.filter(pk=user.pk).update(name="Set in bulk")
    assert user_get(public_id=user.public_id).name != "Set in bulk"

    # ...unless the code that ran it invalidates, as the rule says.
    with cache_writes():
        invalidate(USER_NAMESPACE)
    assert user_get(public_id=user.public_id).name == "Set in bulk"


@pytest.mark.django_db
def test_saving_last_login_with_another_field_still_invalidates(
    user: User,
    cache_writes: CacheWrites,
) -> None:
    before = build_key(USER_NAMESPACE, "probe")

    with cache_writes():
        user.last_login = timezone.now()
        user.name = "Also changed"
        user.save(update_fields=["last_login", "name"])

    assert build_key(USER_NAMESPACE, "probe") != before
