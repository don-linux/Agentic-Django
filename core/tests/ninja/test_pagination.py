from __future__ import annotations

import asyncio
import uuid
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.test import RequestFactory
from django.test import override_settings
from ninja import Schema

from apps.users.models import User
from core.api.pagination import CachedCountPageNumberPagination
from core.api.pagination import CachedCountPaginatorCount
from core.api.pagination import LimitCursorPagination
from core.api.pagination import LimitPageNumberPagination
from core.api.pagination import PageNumberIn
from core.cache import invalidate
from core.tests.ninja.pagination_views import _create_users
from core.tests.ninja.pagination_views import _list
from core.tests.ninja.pagination_views import cached_count_api
from core.tests.ninja.pagination_views import cursor_api
from core.tests.ninja.pagination_views import ordered_api

if TYPE_CHECKING:
    from conftest import CacheWrites

pytestmark = pytest.mark.django_db

MAX_PAGE_SIZE = 100


def test_ordered_list_has_the_pagination_envelope() -> None:
    total, page_size = 3, 2
    _create_users(total)

    response = _list(ordered_api(), f"?page_size={page_size}")
    data = response.json()

    assert set(data) == {"count", "next", "previous", "results"}
    assert data["count"] == total
    assert len(data["results"]) == page_size
    assert data["next"] is not None
    assert data["previous"] is None


def test_page_size_is_capped_at_100() -> None:
    _create_users(MAX_PAGE_SIZE + 1)

    response = _list(ordered_api(), "?page_size=1000")

    assert len(response.json()["results"]) == MAX_PAGE_SIZE


@override_settings(API_MAX_PAGE_NUMBER=2)
@pytest.mark.parametrize("page", ["3", "last"])
def test_pages_beyond_the_cap_are_404(page: str) -> None:
    _create_users(5)

    response = _list(ordered_api(), f"?page_size=1&page={page}")

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json() == {"code": "not_found", "detail": "Invalid page."}


@override_settings(API_MAX_PAGE_NUMBER=2)
def test_the_deepest_allowed_page_still_works() -> None:
    _create_users(5)

    response = _list(ordered_api(), "?page_size=1&page=2")

    assert response.status_code == HTTPStatus.OK
    assert len(response.json()["results"]) == 1


def test_cursor_pagination_follows_next_links_without_a_count() -> None:
    _create_users(3)
    newest_first = list(
        User.objects.order_by("-public_id").values_list("email", flat=True)
    )
    api = cursor_api()

    first = _list(api, "?page_size=2").json()
    cursor = first["next"].split("?", 1)[1]
    second = _list(api, f"?{cursor}").json()

    assert "count" not in first
    assert first["previous"] is None
    emails = [row["email"] for row in first["results"] + second["results"]]
    assert emails == newest_first
    assert second["next"] is None


def test_cursor_pagination_orders_by_public_id_not_by_pk() -> None:
    # public_id is a uuid7 and grows with the pk, so give the rows public_ids in
    # the opposite order: only -public_id, not Ninja's default -pk, lists them
    # by ascending pk.
    _create_users(3)
    by_pk = list(User.objects.order_by("pk").values_list("pk", "email"))
    for position, (pk, _email) in enumerate(by_pk):
        reversed_id = uuid.UUID(int=len(by_pk) - position)
        User.objects.filter(pk=pk).update(public_id=reversed_id)
    api = cursor_api()

    first = _list(api, "?page_size=2").json()
    second = _list(api, f"?{first['next'].split('?', 1)[1]}").json()

    emails = [row["email"] for row in first["results"] + second["results"]]
    assert emails == [email for _pk, email in by_pk]


def test_cursor_parameter_is_documented_in_the_schema() -> None:
    fields = set(LimitCursorPagination.Input.model_fields)
    assert {"cursor", "page_size"} <= fields


def _counts(queries: list[dict[str, str]]) -> int:
    return sum("COUNT(" in query["sql"] for query in queries)


def test_cached_count_runs_the_count_once_per_query(
    cache_writes: CacheWrites,
    django_assert_num_queries,
) -> None:
    total, page_size = 3, 2
    _create_users(total)
    api = cached_count_api()

    with cache_writes(), django_assert_num_queries(2) as first:
        response = _list(api)
    with django_assert_num_queries(1) as second:
        cached = _list(api, f"?page_size={page_size}")

    assert _counts(first.captured_queries) == 1
    assert _counts(second.captured_queries) == 0
    assert response.json()["count"] == cached.json()["count"] == total
    assert len(cached.json()["results"]) == page_size


def test_cached_count_is_counted_again_after_an_invalidation(
    cache_writes: CacheWrites,
    django_assert_num_queries,
) -> None:
    before = 2
    _create_users(before)
    api = cached_count_api()
    with cache_writes():
        _list(api)
    User.objects.bulk_create([User(email="late0@example.com")])

    assert _list(api).json()["count"] == before

    with cache_writes():
        invalidate("users.user")
    with django_assert_num_queries(2) as recount:
        response = _list(api)

    assert _counts(recount.captured_queries) == 1
    assert response.json()["count"] == before + 1


def test_cached_count_is_per_query(cache_writes: CacheWrites) -> None:
    users = 2
    _create_users(users)
    User.objects.create(email="staff@example.com", is_staff=True)
    everyone_api = cached_count_api()
    with cache_writes():
        everyone = _list(everyone_api)

    staff_api = cached_count_api(
        lambda: User.objects.filter(is_staff=True).order_by("pk")
    )
    staff = _list(staff_api)

    assert everyone.json()["count"] == users + 1
    assert staff.json()["count"] == 1


def test_cached_count_of_an_empty_queryset_runs_no_query(
    django_assert_num_queries,
) -> None:
    _create_users(2)
    api = cached_count_api(User.objects.none)

    with django_assert_num_queries(0):
        response = _list(api)

    assert response.json()["count"] == 0
    assert response.json()["results"] == []


def test_cached_count_paginator_counts_a_queryset() -> None:
    total = 3
    _create_users(total)
    paginator = CachedCountPaginatorCount(User.objects.all())

    assert paginator.count == total


def test_page_number_pagination_rejects_a_foreign_input_schema() -> None:
    # paginate_queryset accepts any Schema to match Ninja's base signature and
    # narrows to PageNumberIn; anything else is a wiring bug, not a 404.
    paginator = LimitPageNumberPagination()
    with pytest.raises(TypeError, match="expected PageNumberIn"):
        paginator.paginate_queryset(
            User.objects.none(), Schema(), RequestFactory().get("/")
        )


@pytest.mark.parametrize(
    "paginator", [LimitPageNumberPagination, CachedCountPageNumberPagination]
)
def test_page_number_pagination_refuses_async_views(paginator: type) -> None:
    # Ninja's inherited apaginate_queryset answers {items, count} with no 404 for
    # a bad page: an async view would change the contract without a failing test.
    with pytest.raises(NotImplementedError, match="sync views only"):
        asyncio.run(
            paginator().apaginate_queryset(
                User.objects.none(),
                PageNumberIn(),
                RequestFactory().get("/"),
            )
        )
