"""
Global and scoped DRF throttling.

DRF binds THROTTLE_RATES at import time, so tests patch it. See docs/api.md#throttling.
"""

from __future__ import annotations

import subprocess
from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Any

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.test import force_authenticate
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from config.settings import base as base_settings
from config.settings.guards import assert_throttle_rates
from core.tests.production_settings import load_production_settings
from core.tests.production_settings import load_settings

if TYPE_CHECKING:
    from rest_framework.request import Request

    from apps.users.models import User


class _OpenView(APIView):
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        return Response({"ok": True})


class _ScopedView(_OpenView):
    throttle_scope = "core.test_scope"


class _UnregisteredScopeView(_OpenView):
    throttle_scope = "core.not_registered"


def _rates(monkeypatch: pytest.MonkeyPatch, **rates: str) -> None:
    monkeypatch.setattr(SimpleRateThrottle, "THROTTLE_RATES", rates)


def _get(
    view: type[APIView],
    *,
    user: User | None = None,
    remote_addr: str = "198.51.100.10",
    xff: str | None = None,
) -> Response:
    extra: dict[str, Any] = {"REMOTE_ADDR": remote_addr}
    if xff is not None:
        extra["HTTP_X_FORWARDED_FOR"] = xff
    request = APIRequestFactory().get("/throttled/", **extra)
    if user is not None:
        force_authenticate(request, user=user)
    return view.as_view()(request)


def _with_num_proxies(num_proxies: int) -> override_settings:
    return override_settings(
        REST_FRAMEWORK={**settings.REST_FRAMEWORK, "NUM_PROXIES": num_proxies},
    )


def test_anonymous_limit_returns_429_with_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _rates(monkeypatch, anon="2/min", user="100/min")

    assert _get(_OpenView).status_code == HTTPStatus.OK
    assert _get(_OpenView).status_code == HTTPStatus.OK
    response = _get(_OpenView)

    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert response.data["code"] == "throttled"
    assert int(response.headers["Retry-After"]) > 0


@pytest.mark.django_db
def test_scoped_limit_applies_only_to_its_view(
    monkeypatch: pytest.MonkeyPatch,
    user: User,
) -> None:
    _rates(monkeypatch, anon="100/min", user="100/min", **{"core.test_scope": "1/min"})

    assert _get(_ScopedView, user=user).status_code == HTTPStatus.OK
    throttled = _get(_ScopedView, user=user)
    assert throttled.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert throttled.data["code"] == "throttled"

    # The unscoped view is governed only by the global user rate.
    assert _get(_OpenView, user=user).status_code == HTTPStatus.OK


def test_one_trusted_proxy_ignores_client_forged_xff_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _rates(monkeypatch, anon="2/min", user="100/min")

    with _with_num_proxies(1):
        # Same last hop (appended by our proxy), different forged leading entries.
        for n in range(2):
            response = _get(_OpenView, xff=f"1.2.3.{n}, 203.0.113.7")
            assert response.status_code == HTTPStatus.OK
        response = _get(_OpenView, xff="1.2.3.99, 203.0.113.7")
        assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
        # A different real client has its own bucket.
        assert _get(_OpenView, xff="203.0.113.8").status_code == HTTPStatus.OK


def test_no_trusted_proxy_ignores_xff(monkeypatch: pytest.MonkeyPatch) -> None:
    _rates(monkeypatch, anon="1/min", user="100/min")

    with _with_num_proxies(0):
        assert _get(_OpenView, xff="203.0.113.1").status_code == HTTPStatus.OK
        response = _get(_OpenView, xff="203.0.113.2")
        assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS


def test_base_settings_never_trust_xff_and_enable_global_throttles() -> None:
    assert base_settings.TRUSTED_PROXY_COUNT == 0
    assert base_settings.REST_FRAMEWORK["NUM_PROXIES"] == 0
    assert base_settings.REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"] == (
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.ScopedRateThrottle",
    )


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, 1),
        ({"DJANGO_TRUSTED_PROXY_COUNT": "2"}, 2),
        ({"ALLAUTH_TRUSTED_PROXY_COUNT": "3"}, 3),
        ({"DJANGO_TRUSTED_PROXY_COUNT": "2", "ALLAUTH_TRUSTED_PROXY_COUNT": "3"}, 2),
    ],
)
def test_production_shares_one_proxy_count(env: dict[str, str], expected: int) -> None:
    production = load_production_settings(
        "TRUSTED_PROXY_COUNT",
        "ALLAUTH_TRUSTED_PROXY_COUNT",
        "REST_FRAMEWORK",
        env=env,
    )

    assert production["TRUSTED_PROXY_COUNT"] == expected
    assert production["ALLAUTH_TRUSTED_PROXY_COUNT"] == expected
    assert production["REST_FRAMEWORK"]["NUM_PROXIES"] == expected


VALID_RATES = ["60/min", "5/s", "1000/hour", "10/minute", "1/d"]
# Each one makes DRF's parse_rate raise on every request.
MALFORMED_RATES = ["600 per minute", "sixty/min", "60/", "60/week", "/min"]


@pytest.mark.parametrize("rate", VALID_RATES)
def test_drf_rate_formats_are_accepted(rate: str) -> None:
    assert_throttle_rates({"anon": rate, "reports.generate": None})


@pytest.mark.parametrize("rate", MALFORMED_RATES)
def test_malformed_rates_are_refused(rate: str) -> None:
    with pytest.raises(ImproperlyConfigured, match="'user'"):
        assert_throttle_rates({"user": rate})


def test_a_malformed_rate_in_the_environment_refuses_to_start() -> None:
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        load_settings(
            "config.settings.base",
            "REST_FRAMEWORK",
            env={"DJANGO_THROTTLE_USER_RATE": "600 per minute"},
        )

    assert "DJANGO_THROTTLE_USER_RATE" in excinfo.value.stderr
    assert "600 per minute" not in excinfo.value.stderr
