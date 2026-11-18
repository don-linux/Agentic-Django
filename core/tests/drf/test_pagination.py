from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.test import override_settings
from django.urls import path
from drf_spectacular.generators import SchemaGenerator

from apps.users.models import User
from core.api.pagination import CachedCountPaginator
from core.cache import invalidate
from core.tests.drf.pagination_views import _CachedCountUsersView
from core.tests.drf.pagination_views import _create_users
from core.tests.drf.pagination_views import _CursorUsersView
from core.tests.drf.pagination_views import _list
from core.tests.drf.pagination_views import _OrderedUsersView

if TYPE_CHECKING:
    from conftest import CacheWrites

pytestmark = pytest.mark.django_db

MAX_PAGE_SIZE = 100


class _CachedCountStaffView(_CachedCountUsersView):
    queryset = User.objects.filter(is_staff=True).order_by("pk")


class _CachedCountNobodyView(_CachedCountUsersView):
    queryset = User.objects.none()


def test_ordered_list_has_the_pagination_envelope() -> None:
    total, page_size = 3, 2
    _create_users(total)

    response = _list(_OrderedUsersView, f"?page_size={page_size}")

    assert set(response.data) == {"count", "next", "previous", "results"}
    assert response.data["count"] == total
    assert len(response.data["results"]) == page_size
    assert response.data["next"] is not None
    assert response.data["previous"] is None


def test_page_size_is_capped_at_100() -> None:
    _create_users(MAX_PAGE_SIZE + 1)

    response = _list(_OrderedUsersView, "?page_size=1000")

    assert len(response.data["results"]) == MAX_PAGE_SIZE


@override_settings(REST_FRAMEWORK_MAX_PAGE_NUMBER=2)
@pytest.mark.parametrize("page", ["3", "last"])
def test_pages_beyond_the_cap_are_404(page: str) -> None:
    _create_users(5)

    response = _list(_OrderedUsersView, f"?page_size=1&page={page}")

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.data == {"code": "not_found", "detail": "Invalid page."}


@override_settings(REST_FRAMEWORK_MAX_PAGE_NUMBER=2)
def test_the_deepest_allowed_page_still_works() -> None:
    _create_users(5)

    response = _list(_OrderedUsersView, "?page_size=1&page=2")

    assert response.status_code == HTTPStatus.OK
    assert len(response.data["results"]) == 1


def test_cursor_pagination_follows_next_links_without_a_count() -> None:
    _create_users(3)
    newest_first = list(
        User.objects.order_by("-public_id").values_list("email", flat=True)
    )

    first = _list(_CursorUsersView, "?page_size=2")
    cursor = first.data["next"].split("?", 1)[1]
    second = _list(_CursorUsersView, f"?{cursor}")

    assert "count" not in first.data
    assert first.data["previous"] is None
    emails = [row["email"] for row in first.data["results"] + second.data["results"]]
    assert emails == newest_first
    assert second.data["next"] is None


def test_cursor_parameter_is_documented_in_the_schema() -> None:
    generator = SchemaGenerator(patterns=[path("items/", _CursorUsersView.as_view())])
    schema = generator.get_schema(request=None, public=True)

    parameters = {p["name"] for p in schema["paths"]["/items/"]["get"]["parameters"]}
    assert {"cursor", "page_size"} <= parameters


def _counts(queries: list[dict[str, str]]) -> int:
    return sum("COUNT(" in query["sql"] for query in queries)


def test_cached_count_runs_the_count_once_per_query(
    cache_writes: CacheWrites,
    django_assert_num_queries,
) -> None:
    total = 3
    _create_users(total)

    # The COUNT and the page; the count is stored when the block ends.
    with cache_writes(), django_assert_num_queries(2) as first:
        response = _list(_CachedCountUsersView)
    # Second call: the page only.
    with django_assert_num_queries(1) as second:
        cached = _list(_CachedCountUsersView, "?page_size=2")

    assert _counts(first.captured_queries) == 1
    assert _counts(second.captured_queries) == 0
    assert response.data["count"] == cached.data["count"] == total
    assert len(cached.data["results"]) == 2  # noqa: PLR2004


def test_cached_count_is_counted_again_after_an_invalidation(
    cache_writes: CacheWrites,
    django_assert_num_queries,
) -> None:
    _create_users(2)
    with cache_writes():
        _list(_CachedCountUsersView)
    _create_users_named("late", 1)

    # Stale for up to a minute: the row added behind the cache is not counted...
    assert _list(_CachedCountUsersView).data["count"] == 2  # noqa: PLR2004

    # ...until the code that wrote it invalidates the model's namespace.
    with cache_writes():
        invalidate("users.user")
    with django_assert_num_queries(2) as recount:
        response = _list(_CachedCountUsersView)

    assert _counts(recount.captured_queries) == 1
    assert response.data["count"] == 3  # noqa: PLR2004


def test_cached_count_is_per_query(cache_writes: CacheWrites) -> None:
    _create_users(2)
    User.objects.create(email="staff@example.com", is_staff=True)
    with cache_writes():
        everyone = _list(_CachedCountUsersView)

    # Another filter is another SQL, so another key: no count is shared.
    staff = _list(_CachedCountStaffView)

    assert everyone.data["count"] == 3  # noqa: PLR2004
    assert staff.data["count"] == 1


def test_cached_count_of_an_empty_queryset_runs_no_query(
    django_assert_num_queries,
) -> None:
    _create_users(2)

    with django_assert_num_queries(0):
        response = _list(_CachedCountNobodyView)

    assert response.data["count"] == 0
    assert response.data["results"] == []


def test_cached_count_paginator_counts_a_plain_list() -> None:
    paginator = CachedCountPaginator(["a", "b", "c"], per_page=2)

    assert paginator.count == 3  # noqa: PLR2004
    assert paginator.num_pages == 2  # noqa: PLR2004


def _create_users_named(prefix: str, count: int) -> None:
    User.objects.bulk_create(
        User(email=f"{prefix}{n}@example.com") for n in range(count)
    )
