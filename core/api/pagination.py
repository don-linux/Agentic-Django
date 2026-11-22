"""
List pagination on Ninja's PageNumberPagination, with the
count/next/previous/results envelope.

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
from urllib.parse import parse_qs
from urllib.parse import urlencode
from urllib.parse import urlsplit
from urllib.parse import urlunsplit

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
from pydantic import ValidationError as PydanticValidationError
from pydantic import ValidatorFunctionWrapHandler
from pydantic import field_validator

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

    @field_validator("page", "page_size", mode="wrap")
    @classmethod
    def _lenient(cls, value: Any, handler: ValidatorFunctionWrapHandler) -> Any:
        # paginate_queryset reads the raw query string with DRF's rules.
        try:
            return handler(value)
        except PydanticValidationError:
            return None


def _positive_int(raw: str, cutoff: int) -> int:
    """DRF's _positive_int(strict=True): int(), > 0, capped at cutoff."""
    value = int(raw)
    if value <= 0:
        raise ValueError(raw)
    return min(value, cutoff)


def _set_query_param(url: str, key: str, value: str | None) -> str:
    """DRF's replace_query_param / remove_query_param (sorted keys, lists kept)."""
    scheme, netloc, path, query, fragment = urlsplit(url)
    params = parse_qs(query, keep_blank_values=True)
    if value is None:
        params.pop(key, None)
    else:
        params[key] = [value]
    query = urlencode(sorted(params.items()), doseq=True)
    return urlunsplit((scheme, netloc, path, query, fragment))


class LimitPageNumberPagination(PageNumberPagination):
    """Page-number pagination that matches the current HTTP contract."""

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
        # DRF PageNumberPagination: a bad page_size falls back to the default;
        # an empty page is 1; "last" is the last page; anything else is 404.
        page_size = self.page_size
        if "page_size" in request.GET:
            try:
                page_size = _positive_int(request.GET["page_size"], self.max_page_size)
            except ValueError:
                page_size = self.page_size
        raw_page = request.GET.get("page") or "1"

        count = self.count_items(queryset)
        total_pages = max(1, ceil(count / page_size))
        if raw_page == "last":
            raw_page = str(total_pages)
        try:
            page = int(raw_page)
        except ValueError:
            raise Http404(INVALID_PAGE) from None
        if page > self.max_page_number or page < 1 or page > total_pages:
            raise Http404(INVALID_PAGE)

        offset = (page - 1) * page_size
        results = queryset[offset : offset + page_size]
        url = request.build_absolute_uri()
        next_url = (
            _set_query_param(url, "page", str(page + 1)) if page < total_pages else None
        )
        previous_url = None
        if page > 1:
            previous = page - 1
            # The link to the first page has no page parameter, as DRF's.
            previous_url = _set_query_param(
                url, "page", None if previous == 1 else str(previous)
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
