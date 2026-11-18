from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.urls import Resolver404
from django.urls import resolve
from django.urls import reverse

if TYPE_CHECKING:
    from apps.users.models import User


def test_user_detail(user: User):
    detail_path = f"/api/v1/users/{user.public_id}/"

    assert reverse("api:user-detail", kwargs={"id": user.public_id}) == detail_path
    assert resolve(detail_path).view_name == "api:user-detail"


def test_user_detail_does_not_route_integer_pks(user: User):
    with pytest.raises(Resolver404):
        resolve(f"/api/v1/users/{user.pk}/")


def test_user_list():
    assert reverse("api:user-list") == "/api/v1/users/"
    assert resolve("/api/v1/users/").view_name == "api:user-list"


def test_user_me():
    assert reverse("api:user-me") == "/api/v1/users/me/"
    assert resolve("/api/v1/users/me/").view_name == "api:user-me"
