"""Pins for behaviour mutation testing found unguarded in the users DRF views."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from rest_framework.test import APIRequestFactory

from apps.users.api.views import UserViewSet
from apps.users.models import User

pytestmark = pytest.mark.django_db


def _view_with_user(user: Any) -> UserViewSet:
    view = UserViewSet()
    request: Any = APIRequestFactory().get("/api/v1/users/")
    request.user = user
    view.request = request
    view.format_kwarg = None
    return view


def test_get_queryset_returns_none_when_anonymous() -> None:
    qs = _view_with_user(AnonymousUser()).get_queryset()
    # Must be QuerySet.none(), not an empty filter(pk=None) — that is how
    # deleting the guard, or flipping `or` to `and` on an unsaved user, is killed.
    assert qs.query.is_empty()


def test_get_queryset_returns_none_when_authenticated_pk_is_missing() -> None:
    unsaved = User(email="ghost@example.com")
    assert unsaved.is_authenticated is True
    assert unsaved.pk is None
    qs = _view_with_user(unsaved).get_queryset()
    assert qs.query.is_empty()
