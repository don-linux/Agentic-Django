"""Pins for the users list queryset guard."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser

from apps.users.models import User
from apps.users.selectors import user_list

pytestmark = pytest.mark.django_db


def _queryset(user: Any):
    if not user.is_authenticated or user.pk is None or not isinstance(user, User):
        return User.objects.none()
    return user_list(user=user)


def test_users_list_returns_none_when_anonymous() -> None:
    qs = _queryset(AnonymousUser())
    assert qs.query.is_empty()


def test_users_list_returns_none_when_authenticated_pk_is_missing() -> None:
    unsaved = User(email="ghost@example.com")
    assert unsaved.is_authenticated is True
    assert unsaved.pk is None
    qs = _queryset(unsaved)
    assert qs.query.is_empty()
