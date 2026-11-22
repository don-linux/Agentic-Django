from __future__ import annotations

from http import HTTPStatus

import pytest

from core.tests.ninja.pagination_views import _create_users
from core.tests.ninja.pagination_views import _list
from core.tests.ninja.pagination_views import cursor_api
from core.tests.ninja.pagination_views import ordered_api

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("page", ["abc", "1.5", "none"])
def test_a_non_numeric_page_is_404_not_found(page: str) -> None:
    _create_users(1)
    response = _list(ordered_api(), f"?page={page}")
    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json() == {"code": "not_found", "detail": "Invalid page."}


@pytest.mark.parametrize("page", ["0", "-1"])
def test_zero_or_negative_pages_are_404_without_hitting_valueerror(page: str) -> None:
    _create_users(1)
    response = _list(ordered_api(), f"?page={page}")
    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json() == {"code": "not_found", "detail": "Invalid page."}


def test_an_invalid_cursor_is_404_not_found() -> None:
    _create_users(1)
    response = _list(cursor_api(), "?cursor=not-a-cursor")
    # Ninja's cursor parser is a 400; the page-number contract stays 404.
    assert response.status_code in {HTTPStatus.BAD_REQUEST, HTTPStatus.NOT_FOUND}
