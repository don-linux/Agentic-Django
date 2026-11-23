"""Pin the Ninja call order core.api.throttling relies on (docs/typing.md).

RefreshingRateMixin keeps each check's state on a per-thread copy and reads it
back in wait(). That is only correct while Ninja calls a throttle's wait()
right after its own allow_request() returned False, in the same thread and
with no other allow_request() of that throttle in between (django-ninja 1.8.0
Operation._check_throttles, ninja/operation.py:348-365, sync and async views).
The guide does not document the order (guide/throttling/), so this test fails
if an upgrade changes it.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Any
from typing import override

from ninja import Router
from ninja.testing import TestAsyncClient
from ninja.testing import TestClient

from core.api.throttling import ScopedThrottle
from core.tests.ninja.support import make_api

if TYPE_CHECKING:
    from django.http import HttpRequest
    from ninja import NinjaAPI

REMOTE = {"REMOTE_ADDR": "198.51.100.77"}


class RecordingThrottle(ScopedThrottle):
    def __init__(self, events: list[tuple[str, str, int]], name: str) -> None:
        self.events = events
        self.name = name
        super().__init__("1/min", scope=f"pin-{name}-{uuid.uuid4().hex}")

    @override
    def allow_request(self, request: HttpRequest) -> bool:
        self.events.append(("allow_request", self.name, threading.get_ident()))
        return super().allow_request(request)

    @override
    def wait(self) -> float | None:
        self.events.append(("wait", self.name, threading.get_ident()))
        return super().wait()


def _api(events: list[tuple[str, str, int]], *, is_async: bool) -> NinjaAPI:
    api = make_api(
        throttle=[RecordingThrottle(events, "a"), RecordingThrottle(events, "b")]
    )
    router = Router()
    if is_async:

        @router.get("/pinned/", auth=None)
        async def pinned_async(request: HttpRequest) -> dict[str, Any]:
            return {"ok": True}

    else:

        @router.get("/pinned/", auth=None)
        def pinned(request: HttpRequest) -> dict[str, Any]:
            return {"ok": True}

    api.add_router("/", router)
    return api


def _assert_wait_follows_its_own_allow(events: list[tuple[str, str, int]]) -> None:
    assert [(kind, name) for kind, name, _ in events] == [
        ("allow_request", "a"),
        ("wait", "a"),
        ("allow_request", "b"),
        ("wait", "b"),
    ]
    assert len({thread for _, _, thread in events}) == 1


def test_wait_follows_its_own_allow_request_in_the_same_thread() -> None:
    events: list[tuple[str, str, int]] = []
    client = TestClient(_api(events, is_async=False))
    assert client.get("/pinned/", META=REMOTE).status_code == HTTPStatus.OK
    events.clear()

    response = client.get("/pinned/", META=REMOTE)

    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
    # wait() read the per-thread copy that allow_request() filled.
    assert int(response.headers["Retry-After"]) > 0
    _assert_wait_follows_its_own_allow(events)


def test_async_views_check_throttles_in_the_same_order() -> None:
    events: list[tuple[str, str, int]] = []
    client = TestAsyncClient(_api(events, is_async=True))
    first = asyncio.run(client.get("/pinned/", META=REMOTE))
    assert first.status_code == HTTPStatus.OK
    events.clear()

    response = asyncio.run(client.get("/pinned/", META=REMOTE))

    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert int(response.headers["Retry-After"]) > 0
    _assert_wait_follows_its_own_allow(events)
