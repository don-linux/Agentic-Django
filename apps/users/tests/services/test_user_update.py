from datetime import UTC
from datetime import datetime

import pytest
from django.core.exceptions import ValidationError

from apps.users.models import User
from apps.users.services import user_update

pytestmark = pytest.mark.django_db


def test_user_update(user: User) -> None:
    updated = user_update(user=user, name="Ada Lovelace")

    updated.refresh_from_db()
    assert updated.name == "Ada Lovelace"


def test_user_update_validates_max_length(user: User) -> None:
    with pytest.raises(ValidationError):
        user_update(user=user, name="a" * 256)


def test_user_update_does_not_overwrite_unrelated_columns(user: User) -> None:
    concurrent_login = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    User.objects.filter(pk=user.pk).update(last_login=concurrent_login)
    assert user.last_login is None

    user_update(user=user, name="Ada Lovelace")

    user.refresh_from_db()
    assert user.name == "Ada Lovelace"
    assert user.last_login == concurrent_login
