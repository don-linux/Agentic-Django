"""Support helpers for tests/ninja (not a test module)."""

from __future__ import annotations

from typing import Any

from ninja import NinjaAPI
from ninja import Router
from ninja.testing import TestClient

from core.api.api import ContractAPI
from core.api.exceptions import register_exception_handlers
from core.api.throttling import AnonThrottle
from core.api.throttling import UserThrottle


def make_api(*, throttle=None, auth=None) -> NinjaAPI:
    # The project's API class, so tests see its error mapping (core.api.api).
    api = ContractAPI(
        urls_namespace=f"ninja-test-{id(object())}",
        auth=auth,
        throttle=throttle or [AnonThrottle(), UserThrottle()],
    )
    register_exception_handlers(api)
    return api


def open_get_api(*, throttle=None) -> NinjaAPI:
    api = make_api(throttle=throttle, auth=None)
    router = Router()

    @router.get("/throttled/", auth=None)
    def ok(request):
        return {"ok": True}

    api.add_router("/", router)
    return api


def client_get(
    api: NinjaAPI,
    path: str = "/throttled/",
    *,
    remote_addr: str = "198.51.100.10",
    xff: str | None = None,
    user: Any = None,
):
    headers: dict[str, str] = {}
    if xff is not None:
        headers["X-Forwarded-For"] = xff
    # TestClient uses the META-like headers; REMOTE_ADDR via client_ip or headers.
    client = TestClient(api)
    if user is not None:
        return client.get(
            path, headers=headers, user=user, META={"REMOTE_ADDR": remote_addr}
        )
    return client.get(path, headers=headers, META={"REMOTE_ADDR": remote_addr})
