"""
List pagination on Ninja's PageNumberPagination, with the DRF envelope.

Native Ninja 1.8.0 returns {items, count}, defaults to 100 per page, and does
not 404 past the last page. This class keeps count/next/previous/results,
page_size 20 (max 100), 404 past the last page or DJANGO_API_MAX_PAGE_NUMBER,
so tests/http/ stay unchanged. See docs/api.md#pagination.

Sources: django-ninja 1.8.0 ninja/pagination.py (PageNumberPagination.Output,
items_attribute, paginate_queryset) and Context7 /websites/django-ninja_dev
"Creating a custom pagination class".
"""

from __future__ import annotations

from math import ceil
from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import EmptyResultSet
from django.db.models import QuerySet
from django.http import Http404
from django.http import HttpRequest
from django.utils.functional import cached_property
from ninja import Field
from ninja import Schema
from ninja.pagination import CursorPagination as NinjaCursorPagination
from ninja.pagination import PageNumberPagination

from core.cache import read_through

COUNT_TTL = 60
INVALID_PAGE = "Invalid page."


class PageNumberOut(Schema):
    count: int
    next: str | None = Field(..., json_schema_extra={"format": "uri"})
    previous: str | None = Field(..., json_schema_extra={"format": "uri"})
    results: list[Any]


class PageNumberIn(Schema):
    # Documented as integer (the previous contract). Invalid values such as
    # "abc", 0 or -1 become 404 "Invalid page.": the ValidationError handler
    # maps query page/page_size failures, and paginate_queryset rejects
    # out-of-range numbers (tests/http/).
    page: int | None = Field(
        default=None, description="A page number within the paginated result set."
    )
    page_size: int | None = Field(
        default=None, description="Number of results to return per page."
    )


def _parse_page(raw: int | None) -> int:
    page = 1 if raw is None else raw
    if page < 1:
        raise Http404(INVALID_PAGE)
    return page


def _parse_page_size(raw: int | None, default: int, maximum: int) -> int:
    if raw is None:
        return default
    if raw < 1:
        raise Http404(INVALID_PAGE)
    return min(raw, maximum)


def _abs_url(request: HttpRequest, page: int, page_size: int, default_size: int) -> str:
    params = request.GET.copy()
    params["page"] = str(page)
    if "page_size" in request.GET or page_size != default_size:
        params["page_size"] = str(page_size)
    query = urlencode(params, doseq=True)
    return request.build_absolute_uri(request.path + (f"?{query}" if query else ""))


class LimitPageNumberPagination(PageNumberPagination):
    """Page-number pagination that matches the current HTTP contract."""

    items_attribute = "results"

    # Ninja declares Input/Output as nested classes and its own CursorPagination
    # swaps them for unrelated schemas (django-ninja 1.8.0 ninja/pagination.py);
    # an alias keeps the published component names. Both checkers flag the swap.
    Input = PageNumberIn  # type: ignore[assignment]
    Output = PageNumberOut  # type: ignore[assignment]

    def __init__(
        self,
        page_size: int = 20,
        max_page_size: int = 100,
        **kwargs: Any,
    ) -> None:
        super().__init__(page_size=page_size, max_page_size=max_page_size, **kwargs)

    @property
    def max_page_number(self) -> int:
        return settings.API_MAX_PAGE_NUMBER

    def count_items(self, queryset: Any) -> int:
        if isinstance(queryset, QuerySet):
            return queryset.count()
        return len(queryset)

    def paginate_queryset(
        self,
        queryset: QuerySet,
        pagination: Schema,
        request: HttpRequest,
        **params: Any,
    ) -> dict[str, Any]:
        # Public paginator protocol accepts any Schema; narrow our own Input.
        if not isinstance(pagination, PageNumberIn):
            msg = f"expected PageNumberIn, got {type(pagination).__name__}"
            raise TypeError(msg)
        page = _parse_page(pagination.page)
        page_size = _parse_page_size(
            pagination.page_size, self.page_size, self.max_page_size
        )
        if page > self.max_page_number:
            raise Http404(INVALID_PAGE)

        count = self.count_items(queryset)
        total_pages = ceil(count / page_size) if page_size else 0
        if count == 0:
            if page > 1:
                raise Http404(INVALID_PAGE)
            total_pages = 1
        elif page > total_pages:
            raise Http404(INVALID_PAGE)

        offset = (page - 1) * page_size
        results = queryset[offset : offset + page_size]
        next_url = (
            _abs_url(request, page + 1, page_size, self.page_size)
            if page < total_pages
            else None
        )
        previous_url = (
            _abs_url(request, page - 1, page_size, self.page_size) if page > 1 else None
        )
        return {
            "count": count,
            "next": next_url,
            "previous": previous_url,
            self.items_attribute: results,
        }


class CachedCountPaginatorCount:
    """COUNT(*) cached per query for COUNT_TTL seconds (opt-in)."""

    def __init__(self, queryset: QuerySet) -> None:
        self.queryset = queryset

    @cached_property
    def count(self) -> int:
        queryset = self.queryset
        try:
            sql = str(queryset.query)
        except EmptyResultSet:
            return 0
        return read_through(
            queryset.model._meta.label_lower,  # noqa: SLF001
            "count",
            sql,
            ttl=COUNT_TTL,
            loader=queryset.count,
        )


class CachedCountPageNumberPagination(LimitPageNumberPagination):
    """Page numbers with the total cached for a minute (opt-in, large tables)."""

    def count_items(self, queryset: QuerySet) -> int:
        if not isinstance(queryset, QuerySet):
            return len(queryset)
        return CachedCountPaginatorCount(queryset).count


class LimitCursorPagination(NinjaCursorPagination):
    """
    Cursor pagination for large or append-heavy lists, by -public_id.

    Opt-in. A model without public_id needs its own unique, indexed ordering.
    """

    items_attribute = "results"

    def __init__(
        self,
        page_size: int = 20,
        max_page_size: int = 100,
        **kwargs: Any,
    ) -> None:
        super().__init__(page_size=page_size, max_page_size=max_page_size, **kwargs)
