"""core.api.auth.request_user narrows request.user to the project's user model."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import Http404

from apps.users.models import User
from apps.users.tests.factories import UserFactory
from core.api.auth import request_user

if TYPE_CHECKING:
    from django.test import RequestFactory


@pytest.mark.django_db
def test_the_authenticated_caller_is_returned_as_the_model(rf: RequestFactory) -> None:
    request = rf.get("/")
    user = UserFactory()
    request.user = user
    assert request_user(request, User) is user


def test_an_anonymous_caller_is_a_404(rf: RequestFactory) -> None:
    # Only under auth=None: API_AUTH answers 401 before the view runs.
    request = rf.get("/")
    request.user = AnonymousUser()
    with pytest.raises(Http404):
        request_user(request, User)
