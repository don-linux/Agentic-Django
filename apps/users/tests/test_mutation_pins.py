"""Pins for behaviour mutation testing found unguarded in users."""

from __future__ import annotations

import dataclasses
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from allauth.account.models import EmailAddress
from django.urls import reverse

from apps.users.adapters import AccountAdapter
from apps.users.adapters import HeadlessAdapter
from apps.users.models import User
from apps.users.selectors import user_get
from apps.users.tests.factories import UserFactory

if TYPE_CHECKING:
    from django.test import Client
    from django.test import RequestFactory

    from conftest import CacheWrites

pytestmark = pytest.mark.django_db


def test_user_get_does_not_return_a_different_users_cached_row(
    cache_writes: CacheWrites,
    django_assert_num_queries,
) -> None:
    first = UserFactory(name="Ada")
    second = UserFactory(name="Grace")
    with cache_writes():
        assert user_get(public_id=first.public_id).name == "Ada"
    with django_assert_num_queries(1):
        got = user_get(public_id=second.public_id)
    assert got == second
    assert got.name == "Grace"


def test_user_get_ignores_entries_from_an_older_cache_version(
    user: User,
    cache_writes: CacheWrites,
    django_assert_num_queries,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with cache_writes():
        user_get(public_id=user.public_id)
    monkeypatch.setattr("apps.users.selectors.USER_CACHE_VERSION", 2)
    with django_assert_num_queries(1):
        assert user_get(public_id=user.public_id) == user


def test_create_superuser_stores_the_given_password() -> None:
    user = User.objects.create_superuser(
        email="Admin@Example.com",
        password="something-r@nd0m!",  # noqa: S106
    )
    assert user.check_password("something-r@nd0m!")


def test_create_user_honors_extra_fields() -> None:
    user = User.objects.create_user(
        email="staff@example.com",
        password="something-r@nd0m!",  # noqa: S106
        name="Ada",
        is_staff=True,
    )
    assert user.name == "Ada"
    assert user.is_staff is True
    assert user.is_superuser is False


class TestPatchWithoutName:
    def test_patch_without_name_leaves_the_name_unchanged(
        self,
        user: User,
        user_client: Client,
    ) -> None:
        original = user.name
        for path in (f"/api/v1/users/{user.public_id}/", "/api/v1/users/me/"):
            response = user_client.patch(path, {}, content_type="application/json")
            assert response.status_code == HTTPStatus.OK
            assert response.json()["name"] == original
        user.refresh_from_db()
        assert user.name == original


def test_admin_promotes_an_existing_secondary_email_to_primary(
    admin_client,
) -> None:
    target = UserFactory(email="a@example.com", name="Target")
    EmailAddress.objects.create(
        user=target, email="a@example.com", primary=True, verified=True
    )
    EmailAddress.objects.create(
        user=target, email="b@example.com", primary=False, verified=True
    )
    url = reverse("admin:users_user_change", kwargs={"object_id": target.pk})
    response = admin_client.post(
        url,
        data={
            "email": "B@Example.com",
            "name": "Target",
            "is_active": "on",
            "date_joined_0": "2026-01-01",
            "date_joined_1": "00:00:00",
            "initial-date_joined_0": "2026-01-01",
            "initial-date_joined_1": "00:00:00",
        },
    )
    assert response.status_code == HTTPStatus.FOUND
    target.refresh_from_db()
    assert target.email == "b@example.com"
    promoted = EmailAddress.objects.get(user=target, email="b@example.com")
    former = EmailAddress.objects.filter(user=target, email="a@example.com")
    assert promoted.primary is True
    assert promoted.verified is True
    assert not former.exists() or former.get().primary is False


def test_signup_stays_open_when_the_setting_is_missing(
    rf: RequestFactory, settings
) -> None:
    del settings.ACCOUNT_ALLOW_REGISTRATION
    assert AccountAdapter().is_open_for_signup(rf.get("/")) is True


def test_headless_user_id_is_none_until_the_row_is_saved() -> None:
    unsaved = User(email="unsaved@example.com", name="Draft")
    assert unsaved.pk is None
    data = HeadlessAdapter().user_as_dataclass(unsaved)
    assert data.id is None


def test_headless_user_id_metadata_keeps_example_and_description() -> None:
    user_cls = HeadlessAdapter().get_user_dataclass()
    id_field = next(
        field for field in dataclasses.fields(user_cls) if field.name == "id"
    )
    assert "example" in id_field.metadata
    assert "description" in id_field.metadata
