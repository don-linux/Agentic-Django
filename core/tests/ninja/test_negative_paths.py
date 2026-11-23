from __future__ import annotations

from http import HTTPStatus

import pytest

from core.tests.cache_helpers import DEAD_VALKEY
from core.tests.ninja.pagination_views import _create_users
from core.tests.ninja.pagination_views import _list
from core.tests.ninja.pagination_views import cached_count_api
from core.tests.ninja.pagination_views import cursor_api
from core.tests.ninja.pagination_views import ordered_api

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


def test_an_invalid_cursor_is_400_on_the_cursor_field() -> None:
    _create_users(1)
    response = _list(cursor_api(), "?cursor=not-a-cursor")
    # A cursor is a query field: a bad one is a 400 validation error, like a
    # malformed page number.
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["code"] == "validation_error"
    assert response.json()["fields"] == {
        "cursor": [{"code": "invalid", "detail": "Invalid Cursor"}]
    }


def test_cached_count_list_still_answers_during_a_valkey_outage(settings) -> None:
    settings.CACHES = DEAD_VALKEY
    total = 3
    _create_users(total)
    response = _list(cached_count_api())
    assert response.status_code == HTTPStatus.OK
    assert response.json()["count"] == total
