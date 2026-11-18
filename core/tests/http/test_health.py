from __future__ import annotations

import time
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.conf import settings
from django.db import OperationalError
from django.http import HttpResponse
from django.middleware.security import SecurityMiddleware
from django.test import override_settings
from redis.exceptions import ConnectionError as ValkeyConnectionError

from core.middleware import HealthCheckMiddleware

if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.test import Client
    from django.test import RequestFactory
    from pytest_django.fixtures import Settings

pytestmark = pytest.mark.django_db

# Valkey on a closed port: every call to it fails at once, as during an outage.
DEAD_VALKEY = {
    "default": {
        "BACKEND": "core.cache_backend.ValkeyCache",
        "LOCATION": "redis://127.0.0.1:1/15",
        "OPTIONS": {"socket_connect_timeout": 0.05, "socket_timeout": 0.05},
    },
}
# The timeout of docker/prod/healthcheck.py.
PROBE_TIMEOUT_SECONDS = 3


def test_health_ok(client: Client) -> None:
    response = client.get("/health/", HTTP_HOST="127.0.0.1")

    assert response.status_code == HTTPStatus.OK
    assert response.content == b"ok\n"
    assert response.headers["Cache-Control"] == "no-store"


def test_health_ignores_disallowed_host(client: Client) -> None:
    response = client.get("/health/", HTTP_HOST="not-allowed.example")

    assert response.status_code == HTTPStatus.OK
    assert response.content == b"ok\n"


@override_settings(SECURE_SSL_REDIRECT=True)
def test_health_skips_ssl_redirect(rf: RequestFactory) -> None:
    # SecurityMiddleware reads SECURE_SSL_REDIRECT when it is constructed.
    assert settings.MIDDLEWARE[0] == "core.middleware.HealthCheckMiddleware"

    def downstream(_request: object) -> HttpResponse:
        return HttpResponse("reached")

    security = SecurityMiddleware(downstream)

    def get_response(request: HttpRequest) -> HttpResponse:
        response = security(request)
        if not isinstance(response, HttpResponse):
            message = "expected a synchronous response"
            raise TypeError(message)
        return response

    middleware = HealthCheckMiddleware(get_response)
    health = middleware(rf.get("/health/"))
    other = middleware(rf.get("/api/v1/"))

    assert health.status_code == HTTPStatus.OK
    assert health.content == b"ok\n"
    assert other.status_code == HTTPStatus.MOVED_PERMANENTLY
    assert other["Location"].startswith("https://")


def test_health_rejects_post(client: Client) -> None:
    response = client.post("/health/")

    assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert response.headers["Cache-Control"] == "no-store"


def test_health_database_unavailable(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_cursor(*_args: object, **_kwargs: object) -> None:
        message = "could not connect to server at postgres_db"
        raise OperationalError(message)

    monkeypatch.setattr("core.middleware.connection.cursor", fail_cursor)

    response = client.get("/health/")

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.content == b"database unavailable\n"
    assert response.headers["Cache-Control"] == "no-store"
    assert b"postgres_db" not in response.content


def test_health_cache_unavailable(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_probe() -> None:
        message = "Error 111 connecting to valkey:6379. Connection refused."
        raise ValkeyConnectionError(message)

    monkeypatch.setattr("core.middleware.probe", fail_probe)

    response = client.get("/health/")

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.content == b"cache unavailable\n"
    assert response.headers["Cache-Control"] == "no-store"
    assert b"valkey" not in response.content


def test_health_is_strict_while_the_cache_fails_open(
    client: Client,
    settings: Settings,
) -> None:
    # Ordinary cache calls fail open on this backend; the probe must not, or an
    # outage would never show.
    settings.CACHES = DEAD_VALKEY
    started = time.monotonic()

    response = client.get("/health/")

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.content == b"cache unavailable\n"
    assert time.monotonic() - started < PROBE_TIMEOUT_SECONDS


def test_liveness_does_not_touch_the_cache(client: Client, settings: Settings) -> None:
    settings.CACHES = DEAD_VALKEY

    response = client.get("/health/live/")

    assert response.status_code == HTTPStatus.OK
    assert response.content == b"ok\n"


def test_liveness_does_not_touch_the_database(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_cursor(*_args: object, **_kwargs: object) -> None:
        message = "could not connect to server at postgres_db"
        raise OperationalError(message)

    monkeypatch.setattr("core.middleware.connection.cursor", fail_cursor)

    response = client.get("/health/live/")

    assert response.status_code == HTTPStatus.OK
    assert response.content == b"ok\n"
    assert response.headers["Cache-Control"] == "no-store"


def test_liveness_rejects_post(client: Client) -> None:
    response = client.post("/health/live/")

    assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert response.headers["Cache-Control"] == "no-store"


def test_liveness_ignores_disallowed_host(client: Client) -> None:
    response = client.get("/health/live/", HTTP_HOST="not-allowed.example")

    assert response.status_code == HTTPStatus.OK
    assert response.content == b"ok\n"


def test_health_rejects_non_get_as_plain_text(client: Client) -> None:
    response = client.post("/health/")
    live = client.delete("/health/live/")

    assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert response.headers["Allow"] == "GET"
    assert response.headers["Content-Type"].startswith("text/plain")
    assert live.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert live.headers["Content-Type"].startswith("text/plain")
