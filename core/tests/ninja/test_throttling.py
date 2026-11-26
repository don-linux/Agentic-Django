"""Global and scoped Ninja throttling (docs/api.md#throttling)."""

from __future__ import annotations

import subprocess
from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Any
from typing import override

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest  # noqa: TC002  # runtime: Ninja signature
from django.test import override_settings
from ninja import Router
from ninja.testing import TestClient
from ninja.throttling import BaseThrottle
from ninja.throttling import SimpleRateThrottle

from config.settings import base as base_settings
from config.settings.guards import assert_throttle_rates
from core.api.throttling import AnonThrottle
from core.api.throttling import ScopedThrottle
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
    # The recipe of docs/api.md#throttling: the API-level throttles plus the
    # operation's scope (throttle= replaces them, it does not add to them).
    api = make_api(throttle=[AnonThrottle(), UserThrottle()], auth=None)
    router = Router()

    @router.get(
        "/throttled/",
        auth=None,
        throttle=[AnonThrottle(), UserThrottle(), ScopedThrottle(scope=scope)],
    )
    def ok(request):
        return {"ok": True}

    api.add_router("/", router)
    return api


def test_an_unregistered_scope_fails_when_the_throttle_is_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A scope without a rate in NINJA_DEFAULT_THROTTLE_RATES is a startup
    # error, not an endpoint left unthrottled.
    _rates(monkeypatch, anon="100/min", user="100/min")
    with pytest.raises(ImproperlyConfigured):
        ScopedThrottle(scope="core.unregistered_scope")


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
        # allauth's setting is not an environment variable: only
        # DJANGO_TRUSTED_PROXY_COUNT sets the count.
        ({"ALLAUTH_TRUSTED_PROXY_COUNT": "3"}, 1),
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


# Each valid rate as Ninja 1.8.0 parses it: (requests, seconds).
PARSED_RATES = {
    "60/min": (60, 60),
    "5/s": (5, 1),
    "1000/hour": (1000, 3600),
    "1/d": (1, 86400),
    "1/10s": (1, 10),
    "100/300": (100, 300),
    "5/sec": (5, 1),
}
VALID_RATES = list(PARSED_RATES)
MALFORMED_RATES = [
    "600 per minute",
    "sixty/min",
    "60/",
    "60/week",
    "/min",
    "10/minute",
    "60/hours",
    "60/days",
    "100/hr",
]


@pytest.mark.parametrize("rate", VALID_RATES)
def test_ninja_rate_formats_are_accepted(rate: str) -> None:
    assert_throttle_rates({"anon": rate, "reports.generate": None})
    # The guard and Ninja agree: what the guard accepts, Ninja parses to a
    # positive number of requests per positive number of seconds.
    throttle = SimpleRateThrottle(rate)
    assert (throttle.num_requests, throttle.duration) == PARSED_RATES[rate]


ZERO_RATES = ["0/min", "00/s", "5/0", "5/0s"]


@pytest.mark.parametrize("rate", ZERO_RATES)
def test_a_zero_rate_or_window_is_refused(rate: str) -> None:
    # Ninja parses "0/min" (every request is a 429) and "5/0" (a zero window).
    with pytest.raises(ImproperlyConfigured, match="above zero"):
        assert_throttle_rates({"anon": rate})


@pytest.mark.parametrize("rate", MALFORMED_RATES[5:])
def test_rates_ninja_cannot_parse_are_refused(rate: str) -> None:
    with pytest.raises(ValueError, match="Invalid rate format"):
        SimpleRateThrottle(rate)


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


def test_scoped_throttle_cache_key_names_the_scope(rf) -> None:
    throttle = ScopedThrottle("1/min", scope="uploads")
    request = rf.get("/", REMOTE_ADDR="203.0.113.9")
    request.user = None
    assert throttle.get_cache_key(request) == "throttle_uploads_203.0.113.9"


class _RefusingThrottle(BaseThrottle):
    """Ninja's public custom throttle: refuse, and ask for `seconds` (a float)."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds

    @override
    def allow_request(self, request: HttpRequest) -> bool:
        return False

    @override
    def wait(self) -> float:
        return self.seconds


def _refused_after(seconds: float) -> TestClient:
    api = make_api(throttle=[_RefusingThrottle(seconds)])
    router = Router()

    @router.get("/refused/", auth=None)
    def refused(request: HttpRequest) -> None:
        return None

    api.add_router("/", router)
    return TestClient(api)


@pytest.mark.parametrize(("wait", "seconds"), [(1.2, "2"), (0.4, "1")])
def test_a_fractional_wait_is_rounded_up(wait: float, seconds: str) -> None:
    # Retry-After is integer delta-seconds (RFC 9110): Ninja's _check_throttles
    # rounds the wait up (ninja/operation.py), and the detail is Throttled's text.
    response = _refused_after(wait).get("/refused/")
    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert response["Retry-After"] == seconds
    assert response.json() == {"code": "throttled", "detail": "Too many requests."}
