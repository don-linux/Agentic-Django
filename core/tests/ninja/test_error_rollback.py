"""A handled API error rolls back the request transaction, as DRF's handler did.

Only with ATOMIC_REQUESTS: Django wraps the view in atomic(), and the handler
marks it for rollback (rest_framework.views.set_rollback; Django docs,
"Tying transactions to HTTP requests" and Atomic.set_rollback).
"""

from __future__ import annotations

from http import HTTPStatus

import pytest
from django.db import connection
from django.db import transaction
from ninja import Router
from ninja.testing import TestClient

from apps.users.models import User
from apps.users.tests.factories import UserFactory
from core.exceptions import ApplicationError
from core.tests.ninja.support import make_api

pytestmark = pytest.mark.django_db(transaction=True)


def _client() -> TestClient:
    api = make_api()
    router = Router()

    @router.post("/write-then-fail/", auth=None)
    def write_then_fail(request) -> dict[str, str]:
        UserFactory(email="partial@example.com")
        msg = "Rejected after a write."
        raise ApplicationError(msg, code="rejected")

    api.add_router("/", router)
    return TestClient(api)


def _post_inside_a_request_transaction(client: TestClient) -> int:
    # What django.core.handlers.base.make_view_atomic does with ATOMIC_REQUESTS.
    with transaction.atomic():
        return client.post("/write-then-fail/").status_code


def test_a_handled_error_rolls_back_with_atomic_requests(monkeypatch) -> None:
    monkeypatch.setitem(connection.settings_dict, "ATOMIC_REQUESTS", value=True)
    status = _post_inside_a_request_transaction(_client())
    assert status == HTTPStatus.BAD_REQUEST
    assert not User.objects.filter(email="partial@example.com").exists()


def test_without_atomic_requests_an_outer_atomic_block_is_left_alone() -> None:
    assert connection.settings_dict["ATOMIC_REQUESTS"] is False
    status = _post_inside_a_request_transaction(_client())
    assert status == HTTPStatus.BAD_REQUEST
    assert User.objects.filter(email="partial@example.com").exists()
