"""
List pagination: page numbers by default, plus cursor and cached-count variants.

See docs/api.md#pagination.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings
from django.core.exceptions import EmptyResultSet
from django.core.paginator import Paginator
from django.db.models import QuerySet
from django.utils.functional import cached_property
from rest_framework.exceptions import NotFound
from rest_framework.pagination import CursorPagination
from rest_framework.pagination import PageNumberPagination

from core.cache import read_through

if TYPE_CHECKING:
    from rest_framework.request import Request

# Seconds a cached COUNT(*) may lag behind the table.
COUNT_TTL = 60


class LimitPageNumberPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100

    @property
    def max_page_number(self) -> int:
        # Every page runs COUNT(*) plus OFFSET, which gets slower the deeper it goes.
        return settings.REST_FRAMEWORK_MAX_PAGE_NUMBER

    def get_paginated_response_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        result = super().get_paginated_response_schema(schema)
        properties = result["properties"]
        properties["next"]["example"] = "https://api.example.com/api/v1/users/?page=2"
        properties["previous"]["example"] = (
            "https://api.example.com/api/v1/users/?page=1"
        )
        # DRF always renders every key, with null links on the first and last page.
        result["required"] = list(properties)
        return result

    def get_page_number(self, request: Request, paginator: Paginator) -> Any:
        # Checked after DRF resolves "last", so ?page=last cannot skip the cap.
        page_number = super().get_page_number(request, paginator)
        try:
            too_deep = int(page_number) > self.max_page_number
        except TypeError, ValueError:
            return page_number  # Not a number: the paginator answers 404.
        if too_deep:
            raise NotFound(
                self.invalid_page_message.format(page_number=page_number, message="")
            )
        return page_number


class CachedCountPaginator(Paginator):
    """
    A paginator that caches `COUNT(*)` per query for COUNT_TTL seconds.

    The key is the queryset's SQL (its filters included), in the model's cache
    namespace, so the service's `invalidate()` drops it.
    """

    @cached_property
    def count(self) -> int:
        queryset = self.object_list
        if not isinstance(queryset, QuerySet):
            return len(queryset)
        try:
            sql = str(queryset.query)
        except EmptyResultSet:
            # .none(), or a filter nothing can match: there is no query to run.
            return 0
        return read_through(
            queryset.model._meta.label_lower,  # noqa: SLF001
            "count",
            sql,
            ttl=COUNT_TTL,
            loader=queryset.count,
        )


class CachedCountPageNumberPagination(LimitPageNumberPagination):
    """
    Page numbers with the total cached for a minute (opt-in, for large tables).

    The rows of a page are always read from the database.
    """

    django_paginator_class = CachedCountPaginator


class LimitCursorPagination(CursorPagination):
    """
    Cursor pagination for large or append-heavy lists, by `-public_id`, never the pk.

    A model without `public_id` needs its own unique, indexed `ordering`.
    """

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100
    ordering = "-public_id"

    def get_paginated_response_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        result = super().get_paginated_response_schema(schema)
        # DRF always renders every key, with null links at either end.
        result["required"] = list(result["properties"])
        return result
