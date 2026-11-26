from __future__ import annotations

import asyncio
import base64
from http import HTTPStatus
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import pytest
from django.http import Http404
from ninja.pagination import CursorPagination

from apps.users.models import User
from core.api.pagination import LimitCursorPagination
from core.tests.cache_helpers import DEAD_VALKEY
from core.tests.ninja.pagination_views import _create_users
from core.tests.ninja.pagination_views import _list
from core.tests.ninja.pagination_views import cached_count_api
from core.tests.ninja.pagination_views import cursor_api
from core.tests.ninja.pagination_views import ordered_api

if TYPE_CHECKING:
    from django.test import RequestFactory

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("query", "field", "code"),
    [
        ("page=abc", "page", "int_parsing"),
        ("page=1.5", "page", "int_parsing"),
        ("page=", "page", "int_parsing"),
        ("page=last", "page", "int_parsing"),
        ("page=0", "page", "greater_than_equal"),
        ("page=-1", "page", "greater_than_equal"),
        ("page_size=abc", "page_size", "int_parsing"),
        ("page_size=0", "page_size", "greater_than_equal"),
    ],
)
def test_a_malformed_page_or_page_size_is_a_400_field_error(
    query: str, field: str, code: str
) -> None:
    _create_users(1)
    response = _list(ordered_api(), f"?{query}")
    assert response.status_code == HTTPStatus.BAD_REQUEST
    body = response.json()
    assert body["code"] == "validation_error"
    assert [item["code"] for item in body["fields"][field]] == [code]


@pytest.mark.parametrize(
    "cursor",
    [
        "not-a-cursor",
        # Base64 of "p=abc": it decodes, but -public_id cannot take the position.
        base64.b64encode(b"p=abc").decode(),
    ],
)
def test_a_cursor_the_server_did_not_issue_is_404(cursor: str) -> None:
    # The schema takes any string as a cursor: like a page past the last one, a
    # cursor that names no page is not found.
    _create_users(1)
    response = _list(cursor_api(), f"?{urlencode({'cursor': cursor})}")

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json() == {"code": "not_found", "detail": "Not Found"}


def test_the_async_cursor_pagination_answers_404_too(rf: RequestFactory) -> None:
    pagination = LimitCursorPagination()
    page = CursorPagination.Input(cursor="not-a-cursor")

    with pytest.raises(Http404):
        asyncio.run(
            pagination.apaginate_queryset(User.objects.all(), page, rf.get("/"))
        )


def test_cached_count_list_still_answers_during_a_valkey_outage(settings) -> None:
    settings.CACHES = DEAD_VALKEY
    total = 3
    _create_users(total)
    response = _list(cached_count_api())
    assert response.status_code == HTTPStatus.OK
    assert response.json()["count"] == total
