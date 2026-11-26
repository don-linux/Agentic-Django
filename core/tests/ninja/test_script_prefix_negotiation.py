"""ApiContractMiddleware keeps the contract under a server's script prefix.

WSGIHandler sets Django's script prefix from SCRIPT_NAME (or FORCE_SCRIPT_NAME)
on every request, so reverse() and the paths of the OpenAPI document start
with it, while URL resolution (request.path_info) does not. The middleware
builds its map of operations once per process, on the first request: the map
must not depend on that request's prefix.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.test import override_settings

from core.api.negotiation import _operations_by_route
from core.tests.wsgi import wsgi_request

if TYPE_CHECKING:
    from collections.abc import Iterator

# transaction=True: WSGIHandler sends request_started, whose close_old_connections
# receiver closes the connection; the test Client disconnects it, a server does not.
pytestmark = pytest.mark.django_db(transaction=True)

ME = "/api/v1/users/me/"
METHOD_NOT_ALLOWED = {"code": "method_not_allowed", "detail": "Method not allowed"}


@pytest.fixture(autouse=True)
def _cold_operation_map() -> Iterator[None]:
    # Each test builds the map on its first request, as a new server process
    # does, and leaves none behind for the next test.
    _operations_by_route.cache_clear()
    yield
    _operations_by_route.cache_clear()


@pytest.mark.parametrize("script_name", ["/app", "/nested/app", ""])
def test_a_method_the_route_lacks_is_405(script_name: str) -> None:
    response = wsgi_request("PUT", ME, script_name=script_name)
    assert response.status == HTTPStatus.METHOD_NOT_ALLOWED
    assert response.json() == METHOD_NOT_ALLOWED
    assert response.headers["Allow"] == "GET, PATCH, HEAD, OPTIONS"


def test_options_lists_the_methods_under_a_script_prefix() -> None:
    response = wsgi_request("OPTIONS", ME, script_name="/app")
    assert response.status == HTTPStatus.OK
    assert response.headers["Allow"] == "GET, PATCH, HEAD, OPTIONS"
    assert response.body == b""


def test_an_unacceptable_accept_is_406_under_a_script_prefix() -> None:
    response = wsgi_request(
        "GET", ME, script_name="/app", headers={"Accept": "text/html"}
    )
    assert response.status == HTTPStatus.NOT_ACCEPTABLE
    assert response.json()["code"] == "not_acceptable"


def test_force_script_name_behind_a_proxy() -> None:
    with override_settings(FORCE_SCRIPT_NAME="/app"):
        response = wsgi_request("PUT", ME)
    assert response.status == HTTPStatus.METHOD_NOT_ALLOWED
    assert response.json() == METHOD_NOT_ALLOWED


@pytest.mark.parametrize(("first", "then"), [("/app", ""), ("", "/app")])
def test_the_map_does_not_keep_the_prefix_of_the_first_request(
    first: str, then: str
) -> None:
    # The negative case: whichever prefix built the map, it serves the other.
    assert (
        wsgi_request("PUT", ME, script_name=first).status
        == HTTPStatus.METHOD_NOT_ALLOWED
    )
    response = wsgi_request("PUT", ME, script_name=then)
    assert response.status == HTTPStatus.METHOD_NOT_ALLOWED
    assert response.json() == METHOD_NOT_ALLOWED
