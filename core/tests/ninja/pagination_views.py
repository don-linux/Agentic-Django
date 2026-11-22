"""Standalone list views over User, shared by the pagination tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ninja import NinjaAPI
from ninja import Router
from ninja import Schema
from ninja.pagination import paginate
from ninja.testing import TestClient

from apps.users.models import User
from core.api.exceptions import register_exception_handlers
from core.api.pagination import CachedCountPageNumberPagination
from core.api.pagination import LimitCursorPagination
from core.api.pagination import LimitPageNumberPagination

if TYPE_CHECKING:
    from collections.abc import Callable


class EmailOut(Schema):
    email: str


def _api(
    pagination: type,
    queryset: Callable[[], object],
    *,
    namespace: str,
) -> NinjaAPI:
    api = NinjaAPI(urls_namespace=namespace, auth=None)
    register_exception_handlers(api)
    router = Router()

    @router.get("/", response=list[EmailOut], auth=None)
    @paginate(pagination)
    def listing(request):
        return queryset()

    api.add_router("/", router)
    return api


def _list(api: NinjaAPI, query: str = ""):
    return TestClient(api).get(f"/{query}")


def _create_users(count: int) -> None:
    User.objects.bulk_create(User(email=f"user{n}@example.com") for n in range(count))


def ordered_api() -> NinjaAPI:
    return _api(
        LimitPageNumberPagination,
        lambda: User.objects.order_by("pk"),
        namespace="pag-ordered",
    )


def cached_count_api(queryset=None) -> NinjaAPI:
    source = queryset or (lambda: User.objects.order_by("pk"))
    return _api(
        CachedCountPageNumberPagination,
        source,
        namespace=f"pag-cached-{id(source)}",
    )


def cursor_api() -> NinjaAPI:
    return _api(
        LimitCursorPagination,
        User.objects.all,
        namespace="pag-cursor",
    )
