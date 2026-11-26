"""
List pagination on Ninja's PageNumberPagination and CursorPagination.

Ninja's PageNumberPagination answers {items, count}, serves 100 per page by
default, and does not 404 past the last page. LimitPageNumberPagination
answers count/next/previous/results with absolute links, 20 per page (at most
100), and a 404 past the last page or deeper than DJANGO_API_MAX_PAGE_NUMBER.
page and page_size are validated like Ninja's own Input: a value that is not
a whole number of at least 1 is a 400. See docs/api.md#pagination,
https://django-ninja.dev/guide/pagination/ ("Custom pagination classes") and
django-ninja 1.8.0 ninja/pagination.py.
"""

from __future__ import annotations

from math import ceil
from typing import Any
from typing import override
from urllib.parse import parse_qs
from urllib.parse import urlencode
from urllib.parse import urlsplit
from urllib.parse import urlunsplit

from django.conf import settings
from django.core.exceptions import EmptyResultSet
from django.db.models import QuerySet
from django.http import Http404
from django.http import HttpRequest
from ninja import Field
from ninja import Schema
from ninja.errors import ValidationError
from ninja.pagination import CursorPagination as NinjaCursorPagination
from ninja.pagination import PageNumberPagination

from core.cache import read_through

COUNT_TTL = 60


class PageNumberOut(Schema):
    count: int
    next: str | None = Field(..., json_schema_extra={"format": "uri"})
    previous: str | None = Field(..., json_schema_extra={"format": "uri"})
    results: list[Any]


class PageNumberIn(Schema):
    # ge=1 as in Ninja's PageNumberPagination.Input: "abc", 0 or -1 is a 400
    # validation_error under fields.page or fields.page_size. Both stay
    # nullable: removing null from a published query parameter is an oasdiff
    # ERR (request-parameter-became-not-nullable).
    page: int | None = Field(
        default=None,
        ge=1,
        description="A page number within the paginated result set.",
    )
    page_size: int | None = Field(
        default=None, ge=1, description="Number of results to return per page."
    )


class CursorIn(Schema):
    # Ninja's CursorPagination.Input publishes as "Input" and clamps a page_size
    # below 1 to 1; ge=1 makes it a 400 under fields.page_size, as on a
    # page-number list.
    cursor: str | None = Field(
        default=None,
        description="The page to read, from a next or previous link.",
    )
    page_size: int | None = Field(
        default=None, ge=1, description="Number of results to return per page."
    )


def _set_query_param(url: str, key: str, value: str | None) -> str:
    """Set or remove one query parameter of url; keys sorted, repeated ones kept."""
    scheme, netloc, path, query, fragment = urlsplit(url)
    params = parse_qs(query, keep_blank_values=True)
    if value is None:
        params.pop(key, None)
    else:
        params[key] = [value]
    query = urlencode(sorted(params.items()), doseq=True)
    return urlunsplit((scheme, netloc, path, query, fragment))


class LimitPageNumberPagination(PageNumberPagination):
    """Page numbers with the count/next/previous/results envelope."""

    items_attribute = "results"

    # Ninja declares Input/Output as nested classes and its own CursorPagination
    # swaps them for unrelated schemas (django-ninja 1.8.0 ninja/pagination.py).
    # The alias keeps the component name Ninja publishes from Input, PageNumberIn;
    # a nested `class Input(PageNumberIn)` would publish it as "Input" and pyright
    # would still flag it. Both checkers flag the swap (docs/typing.md).
    Input = PageNumberIn  # type: ignore[assignment]  # pyright: ignore[reportAssignmentType]
    Output = PageNumberOut  # type: ignore[assignment]  # pyright: ignore[reportAssignmentType]

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

    @override
    async def apaginate_queryset(
        self,
        queryset: QuerySet,
        pagination: Schema,
        request: HttpRequest,
        **params: Any,
    ) -> Any:
        # Ninja's inherited async version answers {items, count} with no 404 for
        # a bad page (ninja/pagination.py). Keep the contract to sync views.
        msg = "LimitPageNumberPagination supports sync views only."
        raise NotImplementedError(msg)

    @override
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
        # A page_size above max_page_size is cut to it, as Ninja's
        # PageNumberPagination._get_page_size does.
        page_size = min(pagination.page_size or self.page_size, self.max_page_size)
        page = pagination.page or 1
        # Deeper than DJANGO_API_MAX_PAGE_NUMBER is a 404 before any query:
        # every page runs COUNT(*) and OFFSET (docs/api.md#pagination).
        if page > self.max_page_number:
            raise Http404
        count = self.count_items(queryset)
        total_pages = max(1, ceil(count / page_size))
        if page > total_pages:
            raise Http404

        offset = (page - 1) * page_size
        results = queryset[offset : offset + page_size]
        url = request.build_absolute_uri()
        next_url = (
            _set_query_param(url, "page", str(page + 1)) if page < total_pages else None
        )
        previous_url = None
        if page > 1:
            previous = page - 1
            # The link to the first page has no page parameter.
            previous_url = _set_query_param(
                url, "page", None if previous == 1 else str(previous)
            )
        return {
            "count": count,
            "next": next_url,
            "previous": previous_url,
            self.items_attribute: results,
        }


def _cached_count(queryset: QuerySet) -> int:
    """COUNT(*) of queryset, cached per query for COUNT_TTL seconds."""
    try:
        sql = str(queryset.query)
    except EmptyResultSet:
        # The query can match nothing (none(), an empty __in): no SQL to run.
        return 0
    return read_through(
        queryset.model._meta.label_lower,
        "count",
        sql,
        ttl=COUNT_TTL,
        loader=queryset.count,
    )


class CachedCountPageNumberPagination(LimitPageNumberPagination):
    """Page numbers with the total cached for a minute (opt-in, large tables)."""

    @override
    def count_items(self, queryset: QuerySet) -> int:
        if not isinstance(queryset, QuerySet):
            return len(queryset)
        return _cached_count(queryset)


class LimitCursorPagination(NinjaCursorPagination):
    """
    Cursor pagination for large or append-heavy lists, by -public_id.

    Opt-in. A model without public_id needs its own unique, indexed ordering.
    """

    # As LimitPageNumberPagination.Input: the alias publishes the name CursorIn.
    Input = CursorIn  # type: ignore[assignment]  # pyright: ignore[reportAssignmentType]

    def __init__(
        self,
        page_size: int = 20,
        max_page_size: int = 100,
        ordering: tuple[str, ...] = ("-public_id",),
        **kwargs: Any,
    ) -> None:
        # Ninja's default is ("-pk",): the cursor would carry the integer pk.
        super().__init__(
            page_size=page_size,
            max_page_size=max_page_size,
            ordering=ordering,
            **kwargs,
        )

    # Ninja raises ValidationError([{"cursor": "Invalid Cursor"}]) for a cursor
    # that does not decode, and for one whose position the ordering field cannot
    # take (django-ninja 1.8.0 ninja/pagination.py). The schema accepts any
    # string as a cursor, so that is a 404, as a page past the last one on a
    # page-number list (docs/api.md#pagination).
    @override
    def paginate_queryset(
        self,
        queryset: QuerySet,
        pagination: NinjaCursorPagination.Input,
        request: HttpRequest,
        **params: Any,
    ) -> Any:
        try:
            return super().paginate_queryset(queryset, pagination, request, **params)
        except ValidationError:
            raise Http404 from None

    @override
    async def apaginate_queryset(
        self,
        queryset: QuerySet,
        pagination: NinjaCursorPagination.Input,
        request: HttpRequest,
        **params: Any,
    ) -> Any:
        try:
            return await super().apaginate_queryset(
                queryset, pagination, request, **params
            )
        except ValidationError:
            raise Http404 from None
