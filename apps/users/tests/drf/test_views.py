"""UserViewSet hooks that only DRF calls."""

from __future__ import annotations

from typing import Any
from typing import cast

import pytest

from apps.users.api.serializers import UserSerializer
from apps.users.api.views import UserViewSet

pytestmark = pytest.mark.django_db


def test_perform_update_refuses_a_non_user_instance() -> None:
    # A silent return would answer 200 without saving anything.
    view = UserViewSet()
    not_a_user = cast("Any", object())
    serializer = UserSerializer(instance=not_a_user, data={"name": "x"}, partial=True)

    with pytest.raises(TypeError, match="expected a User instance"):
        view.perform_update(serializer)
