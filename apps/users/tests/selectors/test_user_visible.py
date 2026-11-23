"""user_visible scopes a detail lookup to the requester."""

from __future__ import annotations

import pytest

from apps.users.models import User
from apps.users.selectors import user_visible
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_the_requester_sees_themselves() -> None:
    user = UserFactory()
    assert user_visible(user=user, public_id=user.public_id) == user


def test_another_users_id_does_not_exist_for_the_requester() -> None:
    # The API answers it with 404, like an id that exists nowhere.
    user, other = UserFactory(), UserFactory()
    with pytest.raises(User.DoesNotExist):
        user_visible(user=user, public_id=other.public_id)
