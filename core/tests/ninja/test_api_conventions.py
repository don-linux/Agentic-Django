"""Ninja users routes look up by public_id, never by integer pk."""

from __future__ import annotations

import pytest
from django.urls import Resolver404
from django.urls import resolve

from apps.users.tests.factories import UserFactory


@pytest.mark.django_db
def test_registered_routes_lookup_by_public_id() -> None:
    user = UserFactory()
    match = resolve(f"/api/v1/users/{user.public_id}/")
    assert match.view_name == "api:user-detail"
    with pytest.raises(Resolver404):
        resolve(f"/api/v1/users/{user.pk}/")
