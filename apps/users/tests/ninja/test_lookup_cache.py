"""The detail lookup is reused within one request, never by another one.

Within one request the path validator and the view share one SELECT: the query
budget of a detail PATCH (apps/users/tests/http/test_query_budget.py) pins it.
The entry is keyed weakly by the request (weakref.WeakKeyDictionary), so it goes
away with the request, and a lookup never reads another request's entry.
"""

from __future__ import annotations

import gc
import weakref
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.http import Http404
from django.http import HttpRequest
from django.test import Client
from django.test import RequestFactory

from apps.users.api.views import _VISIBLE_USERS
from apps.users.api.views import _visible_user
from apps.users.tests.factories import UserFactory

if TYPE_CHECKING:
    from apps.users.models import User


@pytest.mark.django_db
def test_a_user_looked_up_by_one_request_is_not_served_to_another() -> None:
    owner = UserFactory.create(name="Before")
    stranger = UserFactory.create()
    owner_client, stranger_client = Client(), Client()
    owner_client.force_login(owner)
    stranger_client.force_login(stranger)
    url = f"/api/v1/users/{owner.public_id}/"

    owned = owner_client.patch(url, {"name": "Owner"}, content_type="application/json")
    refused = stranger_client.patch(
        url, {"name": "Stranger"}, content_type="application/json"
    )

    assert owned.status_code == HTTPStatus.OK
    assert refused.status_code == HTTPStatus.NOT_FOUND
    assert stranger_client.get(url).status_code == HTTPStatus.NOT_FOUND
    owner.refresh_from_db()
    assert owner.name == "Owner"


def _request_as(user: User) -> HttpRequest:
    request = RequestFactory().get("/api/v1/users/")
    request.user = user
    return request


@pytest.mark.django_db
def test_the_entry_goes_away_with_its_request() -> None:
    owner = UserFactory.create()
    request = _request_as(owner)
    assert _visible_user(request, owner.public_id) == owner
    assert request in _VISIBLE_USERS
    released = weakref.ref(request)
    gc.collect()
    entries = len(_VISIBLE_USERS)

    del request
    gc.collect()

    assert released() is None
    assert len(_VISIBLE_USERS) == entries - 1


@pytest.mark.django_db
def test_a_live_entry_is_never_read_by_another_request() -> None:
    owner = UserFactory.create(name="Before")
    stranger = UserFactory.create()
    first = _request_as(owner)
    # first stays referenced until the end, so its entry is live below.
    looked_up = _visible_user(first, owner.public_id)

    # Another user: the owner's live entry must not make the owner visible.
    with pytest.raises(Http404):
        _visible_user(_request_as(stranger), owner.public_id)

    # The same user in a new request reads the database again, not the entry.
    owner.name = "After"
    owner.save(update_fields=["name"])
    fresh = _visible_user(_request_as(owner), owner.public_id)
    assert fresh.name == "After"
    assert looked_up.name == "Before"
    assert first in _VISIBLE_USERS
