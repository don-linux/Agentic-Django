"""
Django's handler400/403/404/500 answer with the API's JSON error body.

See docs/api.md#errors-outside-drf-views.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import Client
from django.test import RequestFactory
from django.test import override_settings
from django.urls import path

from config import urls as project_urls
from core.middleware import ApiDebugErrorMiddleware

if TYPE_CHECKING:
    from django.http import HttpRequest

INTERNAL_DETAIL = "internal state: replica db-2 unreachable"


def _boom(request: HttpRequest) -> HttpResponse:
    raise RuntimeError(INTERNAL_DETAIL)


def _forbidden(request: HttpRequest) -> HttpResponse:
    raise PermissionDenied


# Test-only URLconf: the project routes plus two failing plain Django views. The
# handlers are copied from config.urls, so these tests cover its settings.
urlpatterns = [
    path("boom/", _boom),
    path("forbidden/", _forbidden),
    *project_urls.urlpatterns,
]
handler400 = project_urls.handler400
handler403 = project_urls.handler403
handler404 = project_urls.handler404
handler500 = project_urls.handler500


def _assert_json_error(response: Any, status: HTTPStatus, code: str) -> None:
    # Any: Django's test client response type is private in django-stubs.
    assert response.status_code == status
    assert response.headers["Content-Type"] == "application/json"
    body = response.json()
    assert set(body) == {"code", "detail"}
    assert body["code"] == code


@pytest.mark.django_db
@pytest.mark.parametrize("url", ["/api/v1/does-not-exist/", "/does-not-exist/"])
def test_unknown_route_is_json_404(client: Client, url: str) -> None:
    _assert_json_error(client.get(url), HTTPStatus.NOT_FOUND, "not_found")


@override_settings(ROOT_URLCONF=__name__)
def test_unhandled_exception_is_json_500_without_exception_text() -> None:
    response = Client(raise_request_exception=False).get("/boom/")

    _assert_json_error(response, HTTPStatus.INTERNAL_SERVER_ERROR, "server_error")
    assert INTERNAL_DETAIL not in response.content.decode()


@override_settings(ROOT_URLCONF=__name__)
def test_permission_denied_outside_drf_is_json_403(client: Client) -> None:
    _assert_json_error(
        client.get("/forbidden/"), HTTPStatus.FORBIDDEN, "permission_denied"
    )


def test_disallowed_host_is_json_400(client: Client) -> None:
    response = client.get("/api/v1/users/me/", HTTP_HOST="attacker.invalid")

    _assert_json_error(response, HTTPStatus.BAD_REQUEST, "bad_request")


@pytest.mark.django_db
@override_settings(DEBUG=True)
@pytest.mark.parametrize(
    "url",
    ["/api/v1/does-not-exist/", "/api/v1/users/1/", "/_allauth/does-not-exist"],
)
def test_debug_unknown_api_routes_are_json_404(client: Client, url: str) -> None:
    _assert_json_error(client.get(url), HTTPStatus.NOT_FOUND, "not_found")


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_debug_technical_404_outside_the_api_stays_html(client: Client) -> None:
    response = client.get("/does-not-exist/")

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert "text/html" in response.headers["Content-Type"]


@override_settings(DEBUG=True)
@pytest.mark.parametrize(
    ("status", "code"),
    [
        (HTTPStatus.BAD_REQUEST, "bad_request"),
        (HTTPStatus.FORBIDDEN, "permission_denied"),
        (HTTPStatus.INTERNAL_SERVER_ERROR, "server_error"),
    ],
)
def test_debug_html_errors_on_the_api_become_json(
    rf: RequestFactory, status: HTTPStatus, code: str
) -> None:
    middleware = ApiDebugErrorMiddleware(
        lambda _request: HttpResponse("<html>debug</html>", status=status)
    )

    response = middleware(rf.get("/api/v1/users/"))

    assert response.status_code == status
    assert response.headers["Content-Type"] == "application/json"
    assert json.loads(response.content)["code"] == code


@pytest.mark.parametrize("debug", [False, True])
def test_debug_middleware_leaves_json_and_non_debug_responses_alone(
    rf: RequestFactory,
    debug: bool,  # noqa: FBT001
) -> None:
    body = "<html>prod</html>" if not debug else '{"code": "x"}'
    content_type = "text/html" if not debug else "application/json"
    original = HttpResponse(
        body, status=HTTPStatus.NOT_FOUND, content_type=content_type
    )
    middleware = ApiDebugErrorMiddleware(lambda _request: original)

    with override_settings(DEBUG=debug):
        assert middleware(rf.get("/api/v1/users/")) is original


@override_settings(DEBUG=True)
@pytest.mark.parametrize("status", [299, 499])
def test_debug_middleware_passes_non_standard_status_codes_through(
    rf: RequestFactory, status: int
) -> None:
    middleware = ApiDebugErrorMiddleware(
        lambda _request: HttpResponse("custom", status=status)
    )

    response = middleware(rf.get("/api/v1/users/"))

    assert response.status_code == status
    assert response.content == b"custom"
