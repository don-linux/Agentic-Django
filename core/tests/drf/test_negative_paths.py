"""Negative paths that need DRF internals: paginator views and exception details."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Any

import pytest
from rest_framework.exceptions import APIException
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from core.tests.cache_helpers import DEAD_VALKEY
from core.tests.drf.pagination_views import _CachedCountUsersView
from core.tests.drf.pagination_views import _create_users
from core.tests.drf.pagination_views import _CursorUsersView
from core.tests.drf.pagination_views import _list
from core.tests.drf.pagination_views import _OrderedUsersView

if TYPE_CHECKING:
    from pytest_django.fixtures import Settings


@pytest.mark.django_db
def test_cached_count_list_still_answers_during_a_valkey_outage(
    settings: Settings,
) -> None:
    settings.CACHES = DEAD_VALKEY
    _create_users(3)

    response = _list(_CachedCountUsersView)

    assert response.status_code == HTTPStatus.OK
    assert response.data["count"] == 3  # noqa: PLR2004


@pytest.mark.django_db
@pytest.mark.parametrize("page", ["abc", "1e2"])
def test_a_non_numeric_page_is_404_not_found(page: str) -> None:
    _create_users(3)
    response = _list(_OrderedUsersView, f"?page_size=1&page={page}")

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.data == {"code": "not_found", "detail": "Invalid page."}


@pytest.mark.django_db
@pytest.mark.parametrize("page", ["-1", "0"])
def test_zero_or_negative_pages_are_404_without_hitting_valueerror(
    page: str,
) -> None:
    # int("-1") and int("0") succeed: these do NOT take the except branch.
    _create_users(3)
    response = _list(_OrderedUsersView, f"?page_size=1&page={page}")

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.data["code"] == "not_found"


@pytest.mark.django_db
def test_an_invalid_cursor_is_404_not_found() -> None:
    _create_users(3)
    response = _list(_CursorUsersView, "?cursor=not-a-cursor")

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.data == {"code": "not_found", "detail": "Invalid cursor"}


class _ListDetailView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list[Any] = []

    def post(self, request: Any) -> Any:
        raise APIException(detail=["one", "two"])


class _DictDetailView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list[Any] = []

    def post(self, request: Any) -> Any:
        raise APIException(detail={"foo": "bar"})


class _AuthFailedListView(APIView):
    permission_classes = [AllowAny]
    # Keep default authenticators so authenticate_header yields 401, not 403.

    def post(self, request: Any) -> Any:
        raise AuthenticationFailed(detail=["bad token"])


def _call(view: type[APIView]) -> Any:
    request = APIRequestFactory().post("/test/", {}, format="json")
    return view.as_view()(request)


@pytest.mark.parametrize("view", [_ListDetailView, _DictDetailView])
def test_api_exception_with_list_or_dict_detail_keeps_the_uniform_body(
    view: type[APIView],
) -> None:
    response = _call(view)

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert set(response.data) <= {"code", "detail"}
    assert response.data == {
        "code": "error",
        "detail": "A server error occurred.",
    }


def test_authentication_failed_list_detail_keeps_defaults() -> None:
    response = _call(_AuthFailedListView)

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.data == {
        "code": "authentication_failed",
        "detail": "Incorrect authentication credentials.",
    }
