"""An error an operation does not handle answers the same status with DEBUG on.

Ninja's default exception handler answers a text/plain 500 with DEBUG on and
re-raises with it off (ninja/errors.py, _default_exception). The API re-raises
always (core.api.exceptions), so Django answers either way: a
SuspiciousOperation is 400, anything else 500, both in JSON on /api/
(ApiDebugErrorMiddleware with DEBUG on), and ATOMIC_REQUESTS rolls back.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.db import connection
from django.test import Client

from apps.users.models import User
from apps.users.tests.factories import UserFactory

if TYPE_CHECKING:
    from pytest_django.fixtures import Settings

# transaction=True: the request's own atomic block is the outermost one, as on
# a server, so a rollback leaves nothing behind.
pytestmark = pytest.mark.django_db(transaction=True)

ME = "/api/v1/users/me/"


def _client(user: User) -> Client:
    client = Client(raise_request_exception=False)
    client.force_login(user)
    return client


@pytest.mark.parametrize("debug", [False, True])
def test_a_body_over_the_memory_limit_is_400(
    settings: Settings, user: User, *, debug: bool
) -> None:
    settings.DEBUG = debug
    body = json.dumps({"name": "x" * (settings.DATA_UPLOAD_MAX_MEMORY_SIZE + 1)})
    response = _client(user).patch(ME, data=body, content_type="application/json")
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["code"] == "bad_request"


@pytest.mark.parametrize("debug", [False, True])
def test_an_unhandled_error_is_500_and_rolls_back(
    settings: Settings,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    *,
    debug: bool,
) -> None:
    settings.DEBUG = debug
    monkeypatch.setitem(connection.settings_dict, "ATOMIC_REQUESTS", value=True)

    def write_then_fail(**_kwargs: object) -> User:
        UserFactory(email="partial@example.com")
        msg = "Failed after a write."
        raise RuntimeError(msg)

    monkeypatch.setattr("apps.users.api.views.user_update", write_then_fail)
    response = _client(user).patch(
        ME, data={"name": "Ada"}, content_type="application/json"
    )
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert response.json()["code"] == "server_error"
    assert not User.objects.filter(email="partial@example.com").exists()
