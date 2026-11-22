"""Guards on the users views that only the view layer can enforce."""

from __future__ import annotations

from typing import Any
from typing import cast

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import Http404
from django.test import RequestFactory

from apps.users.api.views import _apply_update
from apps.users.api.views import users_me_retrieve

pytestmark = pytest.mark.django_db


def test_apply_update_refuses_a_non_user_instance() -> None:
    not_a_user = cast("Any", object())

    with pytest.raises(TypeError):
        _apply_update(not_a_user, {"name": "x"})


def test_me_retrieve_refuses_a_request_without_a_user() -> None:
    # The API auth lets only users through; without one the view is a 404, never
    # an AnonymousUser serialized as a user.
    request = RequestFactory().get("/api/v1/users/me/")
    request.user = AnonymousUser()

    with pytest.raises(Http404):
        users_me_retrieve(request)
