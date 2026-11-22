"""Global and scoped Ninja throttling (docs/api.md#throttling)."""

from __future__ import annotations

import subprocess
from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from ninja import Router
from ninja.throttling import SimpleRateThrottle

from config.settings import base as base_settings
from config.settings.guards import assert_throttle_rates
from core.api.throttling import AnonThrottle
from core.api.throttling import UserThrottle
from core.tests.ninja.support import client_get
from core.tests.ninja.support import make_api
from core.tests.production_settings import load_production_settings
from core.tests.production_settings import load_settings

if TYPE_CHECKING:
    from apps.users.models import User


def _rates(monkeypatch: pytest.MonkeyPatch, **rates: str) -> None:
    monkeypatch.setattr(SimpleRateThrottle, "THROTTLE_RATES", rates)


def _open_api() -> Any:
    api = make_api(throttle=[AnonThrottle(), UserThrottle()], auth=None)
    router = Router()

    @router.get("/throttled/", auth=None)
    def ok(request):
        return {"ok": True}

    api.add_router("/", router)
    return api


def _scoped_api(scope: str) -> Any:
    class Scoped(SimpleRateThrottle):
        def __init__(self) -> None:
            self.scope = scope
            super().__init__()

        def get_cache_key(self, request):
            return self.cache_format % {
                "scope": self.scope,
                "ident": self.get_ident(request),
            }

    api = make_api(throttle=[Scoped()], auth=None)
    router = Router()

    @router.get("/throttled/", auth=None)
    def ok(request):
        return {"ok": True}

    api.add_router("/", router)
    return api


def test_anonymous_limit_returns_429_with_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _rates(monkeypatch, anon="2/min", user="100/min")
    api = _open_api()

    assert client_get(api).status_code == HTTPStatus.OK
    assert client_get(api).status_code == HTTPStatus.OK
    response = client_get(api)

    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert response.json()["code"] == "throttled"
    assert int(response.headers["Retry-After"]) > 0


@pytest.mark.django_db
def test_scoped_limit_applies_only_to_its_view(
    monkeypatch: pytest.MonkeyPatch,
    user: User,
) -> None:
    _rates(monkeypatch, anon="100/min", user="100/min", **{"core.test_scope": "1/min"})
    scoped = _scoped_api("core.test_scope")
    unscoped = _open_api()

    assert client_get(scoped, user=user).status_code == HTTPStatus.OK
    throttled = client_get(scoped, user=user)
    assert throttled.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert throttled.json()["code"] == "throttled"
    assert client_get(unscoped, user=user).status_code == HTTPStatus.OK


def _with_num_proxies(num_proxies: int) -> override_settings:
    return override_settings(
        TRUSTED_PROXY_COUNT=num_proxies,
        NINJA_NUM_PROXIES=num_proxies,
    )


def test_one_trusted_proxy_ignores_client_forged_xff_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _rates(monkeypatch, anon="2/min", user="100/min")
    api = _open_api()

    with _with_num_proxies(1):
        for n in range(2):
            response = client_get(api, xff=f"1.2.3.{n}, 203.0.113.7")
            assert response.status_code == HTTPStatus.OK
        response = client_get(api, xff="1.2.3.99, 203.0.113.7")
        assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
        assert client_get(api, xff="203.0.113.8").status_code == HTTPStatus.OK


def test_no_trusted_proxy_ignores_xff(monkeypatch: pytest.MonkeyPatch) -> None:
    _rates(monkeypatch, anon="1/min", user="100/min")
    api = _open_api()

    with _with_num_proxies(0):
        assert client_get(api, xff="203.0.113.1").status_code == HTTPStatus.OK
        response = client_get(api, xff="203.0.113.2")
        assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS


def test_base_settings_never_trust_xff_and_enable_global_throttles() -> None:
    assert base_settings.TRUSTED_PROXY_COUNT == 0
    assert base_settings.NINJA_NUM_PROXIES == 0
    assert set(base_settings.NINJA_DEFAULT_THROTTLE_RATES) >= {"anon", "user"}


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
        "NINJA_NUM_PROXIES",
        env=env,
    )

    assert production["TRUSTED_PROXY_COUNT"] == expected
    assert production["ALLAUTH_TRUSTED_PROXY_COUNT"] == expected
    assert production["NINJA_NUM_PROXIES"] == expected


VALID_RATES = ["60/min", "5/s", "1000/hour", "10/minute", "1/d"]
MALFORMED_RATES = ["600 per minute", "sixty/min", "60/", "60/week", "/min"]


@pytest.mark.parametrize("rate", VALID_RATES)
def test_ninja_rate_formats_are_accepted(rate: str) -> None:
    assert_throttle_rates({"anon": rate, "reports.generate": None})


@pytest.mark.parametrize("rate", MALFORMED_RATES)
def test_malformed_rates_are_refused(rate: str) -> None:
    with pytest.raises(ImproperlyConfigured, match="'user'"):
        assert_throttle_rates({"user": rate})


def test_a_malformed_rate_in_the_environment_refuses_to_start() -> None:
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        load_settings(
            "config.settings.base",
            "NINJA_DEFAULT_THROTTLE_RATES",
            env={"DJANGO_THROTTLE_USER_RATE": "600 per minute"},
        )

    assert "DJANGO_THROTTLE_USER_RATE" in excinfo.value.stderr
    assert "600 per minute" not in excinfo.value.stderr
