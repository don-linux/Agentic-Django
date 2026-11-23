from __future__ import annotations

from typing import TYPE_CHECKING
from unittest import mock

import pytest
from allauth.account.models import EmailAddress
from allauth.mfa.models import Authenticator
from django.contrib.admin.models import CHANGE
from django.contrib.admin.models import LogEntry
from django.contrib.contenttypes.models import ContentType
from django.contrib.sessions.backends.cached_db import SessionStore
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError

from apps.users.models import User
from apps.users.selectors import user_get
from apps.users.services import user_anonymize
from apps.users.tests.factories import UserFactory
from core.tests.credentials import PASSWORD

if TYPE_CHECKING:
    from conftest import CacheWrites

pytestmark = pytest.mark.django_db

EMAIL = "erase-me@example.com"


def _verified_user() -> User:
    user = UserFactory(email=EMAIL, name="Ada Lovelace", password=PASSWORD)
    EmailAddress.objects.create(user=user, email=EMAIL, primary=True, verified=True)
    return user


def test_anonymize_erases_personal_data_and_keeps_the_row() -> None:
    user = _verified_user()
    public_id = user.public_id

    user_anonymize(user=user)

    user.refresh_from_db()
    assert user.public_id == public_id
    assert user.email == f"deleted-{public_id}@anonymized.invalid"
    assert user.name == ""
    assert user.last_login is None
    assert user.has_usable_password() is False
    assert user.is_active is False


def test_anonymize_removes_addresses_and_authenticators() -> None:
    user = _verified_user()
    Authenticator.objects.create(user=user, type=Authenticator.Type.TOTP, data={})

    user_anonymize(user=user)

    assert not EmailAddress.objects.filter(user=user).exists()
    assert not Authenticator.objects.filter(user=user).exists()


def test_anonymize_signs_the_user_out_and_frees_the_email() -> None:
    user = _verified_user()
    store = SessionStore()
    store["_auth_user_id"] = str(user.pk)
    store.create()

    user_anonymize(user=user)

    assert not Session.objects.filter(session_key=store.session_key).exists()
    assert not User.objects.filter(email=EMAIL).exists()
    UserFactory(email=EMAIL)


def test_anonymize_scrubs_the_admin_history() -> None:
    user = _verified_user()
    LogEntry.objects.create(
        user=UserFactory(is_staff=True),
        content_type=ContentType.objects.get_for_model(User),
        object_id=str(user.pk),
        object_repr=str(user),
        action_flag=CHANGE,
    )

    user_anonymize(user=user)

    assert list(LogEntry.objects.values_list("object_repr", flat=True)) == [
        f"deleted-{user.public_id}@anonymized.invalid",
    ]


def test_anonymize_scrubs_the_admin_history_of_the_email_addresses() -> None:
    user = _verified_user()
    address = EmailAddress.objects.get(user=user)
    LogEntry.objects.create(
        user=UserFactory(is_staff=True),
        content_type=ContentType.objects.get_for_model(EmailAddress),
        object_id=str(address.pk),
        object_repr=str(address),
        action_flag=CHANGE,
    )

    user_anonymize(user=user)

    assert list(LogEntry.objects.values_list("object_repr", flat=True)) == [
        f"deleted-{user.public_id}@anonymized.invalid",
    ]


def test_anonymize_invalidates_the_cached_user(cache_writes: CacheWrites) -> None:
    user = _verified_user()
    with cache_writes():
        user_get(public_id=user.public_id)

    with cache_writes():
        user_anonymize(user=user)

    assert user_get(public_id=user.public_id).email.startswith("deleted-")


def test_anonymize_rolls_back_when_full_clean_fails() -> None:
    user = _verified_user()

    # The first full_clean is user_deactivate's; the second, anonymize's own.
    with (
        mock.patch.object(
            User, "full_clean", side_effect=[None, ValidationError("bad")]
        ),
        pytest.raises(ValidationError),
    ):
        user_anonymize(user=user)

    user.refresh_from_db()
    assert user.email == EMAIL
    assert user.is_active is True
    assert EmailAddress.objects.filter(user=user).exists()
