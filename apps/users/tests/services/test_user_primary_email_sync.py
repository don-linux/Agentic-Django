from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from allauth.account.models import EmailAddress

from apps.users.services import user_primary_email_sync

if TYPE_CHECKING:
    from apps.users.models import User

pytestmark = pytest.mark.django_db


def _addresses(user: User) -> list[tuple[str, bool, bool]]:
    return sorted(
        EmailAddress.objects.filter(user=user).values_list(
            "email", "primary", "verified"
        )
    )


def test_a_user_without_addresses_gets_a_primary_verified_one(user: User) -> None:
    user_primary_email_sync(user=user)

    assert _addresses(user) == [(user.email, True, True)]


def test_another_primary_address_is_replaced(user: User) -> None:
    EmailAddress.objects.create(
        user=user, email="old@example.com", primary=True, verified=True
    )

    user_primary_email_sync(user=user)

    assert _addresses(user) == [(user.email, True, True)]


def test_an_existing_secondary_address_is_promoted_as_it_is(user: User) -> None:
    EmailAddress.objects.create(
        user=user, email=user.email, primary=False, verified=False
    )

    user_primary_email_sync(user=user)

    # Promoted, not re-verified: only the user's own confirmation verifies it.
    assert _addresses(user) == [(user.email, True, False)]


def test_running_it_twice_changes_nothing(user: User) -> None:
    user_primary_email_sync(user=user)
    user_primary_email_sync(user=user)

    assert _addresses(user) == [(user.email, True, True)]


def test_verify_marks_an_existing_unverified_address_as_verified(user: User) -> None:
    EmailAddress.objects.create(
        user=user, email=user.email, primary=False, verified=False
    )

    user_primary_email_sync(user=user, verify=True)

    assert _addresses(user) == [(user.email, True, True)]
