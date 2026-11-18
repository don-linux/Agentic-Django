"""
What only a real Valkey shows under DRF: two workers share one throttle counter.

Marker `valkey`: this needs the dev-stack Valkey and fails, never skips, without it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from rest_framework.test import APIRequestFactory
from rest_framework.test import force_authenticate
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

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
    # With a per-process cache each gunicorn worker would count on its own and
    # the real limit would be the rate times the number of workers.
    monkeypatch.setattr(SimpleRateThrottle, "THROTTLE_RATES", {"user": "2/min"})
    workers = [valkey, second_worker()]
    request = APIRequestFactory().get("/")
    force_authenticate(request, user=user)
    drf_request = APIView().initialize_request(request)

    allowed = []
    for attempt in range(3):
        throttle = UserRateThrottle()
        throttle.cache = workers[attempt % 2]
        allowed.append(throttle.allow_request(drf_request, APIView()))

    assert allowed == [True, True, False]
