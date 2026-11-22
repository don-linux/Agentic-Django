"""Two workers share one throttle counter on a real Valkey (marker valkey)."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from ninja import Router
from ninja.throttling import SimpleRateThrottle
from ninja.throttling import UserRateThrottle

from core.tests.ninja.support import client_get
from core.tests.ninja.support import make_api

if TYPE_CHECKING:
    from collections.abc import Callable

    from apps.users.models import User
    from core.cache_backend import ValkeyCache

pytestmark = [pytest.mark.valkey, pytest.mark.django_db]


def test_two_workers_share_one_throttle_counter(
    valkey: ValkeyCache,
    second_worker: Callable[[], ValkeyCache],
    monkeypatch: pytest.MonkeyPatch,
    user: User,
) -> None:
    monkeypatch.setattr(SimpleRateThrottle, "THROTTLE_RATES", {"user": "2/min"})
    workers = [valkey, second_worker()]

    class SharedUserThrottle(UserRateThrottle):
        def __init__(self, cache) -> None:
            super().__init__()
            self.cache = cache

    allowed = []
    for attempt in range(3):
        throttle = SharedUserThrottle(workers[attempt % 2])
        api = make_api(throttle=[throttle], auth=None)
        router = Router()

        @router.get("/throttled/", auth=None)
        def ok(request):
            return {"ok": True}

        api.add_router("/", router)
        allowed.append(
            client_get(api, user=user).status_code != HTTPStatus.TOO_MANY_REQUESTS
        )

    assert allowed == [True, True, False]
