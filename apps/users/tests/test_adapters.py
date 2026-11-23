from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

import pytest
from allauth.headless.adapter import get_adapter
from django.test import override_settings

from apps.users.adapters import AccountAdapter
from apps.users.adapters import HeadlessAdapter
from apps.users.models import User
from apps.users.tests.factories import UserFactory

if TYPE_CHECKING:
    from django.test import RequestFactory
    from pytest_django.fixtures import Settings


@pytest.mark.parametrize("allowed", [True, False])
def test_account_signup_follows_registration_setting(
    rf: RequestFactory,
    *,
    allowed: bool,
) -> None:
    with override_settings(ACCOUNT_ALLOW_REGISTRATION=allowed):
        assert AccountAdapter().is_open_for_signup(rf.get("/")) is allowed


def test_signup_stays_open_when_the_setting_is_missing(
    rf: RequestFactory, settings: Settings
) -> None:
    del settings.ACCOUNT_ALLOW_REGISTRATION

    assert AccountAdapter().is_open_for_signup(rf.get("/")) is True


def test_the_headless_user_id_is_documented_as_a_uuid_string() -> None:
    user_dataclass = get_adapter().get_user_dataclass()
    [id_field] = [f for f in dataclasses.fields(user_dataclass) if f.name == "id"]

    assert id_field.type == str | None
    assert id_field.metadata["description"] == "The public user ID (UUID)."
    assert "example" in id_field.metadata


@pytest.mark.django_db
def test_the_headless_user_id_is_none_until_the_user_is_saved() -> None:
    unsaved = User(email="unsaved@example.com", name="Draft")

    assert HeadlessAdapter().user_as_dataclass(unsaved).id is None


PHONE = "+525512345678"


@pytest.mark.django_db
def test_the_adapter_stores_and_finds_no_phone() -> None:
    adapter = AccountAdapter()
    user = UserFactory()

    assert adapter.get_phone(user) is None
    assert adapter.get_user_by_phone(PHONE) is None
    for call in (
        lambda: adapter.set_phone(user, PHONE, verified=True),
        lambda: adapter.set_phone_verified(user, PHONE),
        lambda: adapter.send_verification_code_sms(user, PHONE, "123456"),
    ):
        with pytest.raises(NotImplementedError, match=r"docs/auth/phone\.md"):
            call()
