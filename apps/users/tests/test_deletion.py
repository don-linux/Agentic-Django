"""Users are never deleted through the ORM (docs/user-deletion/)."""

from __future__ import annotations

import pytest
from django.db.models import ProtectedError

from apps.users.models import User
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_user_delete_is_refused() -> None:
    user = UserFactory()

    with pytest.raises(ProtectedError, match="hard_delete_user"):
        user.delete()

    assert User.objects.filter(pk=user.pk).exists()


@pytest.mark.parametrize("scope", ["filter", "all"])
def test_queryset_delete_is_refused(scope: str) -> None:
    user = UserFactory()
    queryset = (
        User.objects.filter(pk=user.pk) if scope == "filter" else User.objects.all()
    )

    with pytest.raises(ProtectedError):
        queryset.delete()

    assert User.objects.filter(pk=user.pk).exists()
