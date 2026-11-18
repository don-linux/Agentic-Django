"""django-zeal fails N+1 queries in tests (config/settings/test.py)."""

from __future__ import annotations

import pytest
from zeal import zeal_context
from zeal.errors import NPlusOneError

from apps.users.models import User
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_zeal_raises_on_an_n_plus_one() -> None:
    UserFactory.create_batch(3)

    with pytest.raises(NPlusOneError), zeal_context():  # noqa: PT012
        for user in User.objects.all():
            list(user.emailaddress_set.all())  # type: ignore[attr-defined]
