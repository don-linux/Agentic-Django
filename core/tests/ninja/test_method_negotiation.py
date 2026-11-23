"""Allow, 405, HEAD and OPTIONS come from the registered operations.

core.api.negotiation used to hard-code the users routes: any path under
/api/v1/users/ answered 405 to a write (even one that is not a route, which
must be 404), and a new router got Ninja's text/plain 405 and no HEAD.
"""

from __future__ import annotations

from collections import Counter
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.urls import URLPattern
from django.urls import URLResolver
from django.urls import get_resolver

from core.api.api import api
from core.api.negotiation import _allow_header

if TYPE_CHECKING:
    from django.test import Client

ME = "/api/v1/users/me/"


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["post", "put", "delete"])
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/users/1/",
        "/api/v1/users/00000000-0000-4000-8000-000000000000/extra/",
        "/api/v1/nothing/",
    ],
)
def test_a_path_that_is_not_a_route_is_404_for_every_method(
    user_client: Client, method: str, path: str
) -> None:
    response = getattr(user_client, method)(path)
    assert response.status_code == HTTPStatus.NOT_FOUND
    assert "Allow" not in response


@pytest.mark.django_db
def test_a_method_the_route_lacks_is_a_json_405_with_allow(user_client: Client) -> None:
    response = user_client.delete(ME)
    assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert response["Allow"] == "GET, PATCH, HEAD, OPTIONS"
    assert response.json() == {
        "code": "method_not_allowed",
        "detail": "Method not allowed",
    }


@pytest.mark.django_db
def test_allow_goes_on_the_405_and_options_only(
    client: Client, user_client: Client
) -> None:
    # As Django and Ninja: RFC 9110 requires Allow on a 405 and OPTIONS lists the
    # methods; the operation's own answers, a 401 and a 406 do not carry it.
    ok = user_client.get(ME)
    unauthenticated = client.get(ME)
    not_acceptable = user_client.get(ME, headers={"Accept": "text/html"})
    not_allowed = user_client.delete(ME)
    options = client.options(ME)

    assert ok.status_code == HTTPStatus.OK
    assert unauthenticated.status_code == HTTPStatus.UNAUTHORIZED
    assert not_acceptable.status_code == HTTPStatus.NOT_ACCEPTABLE
    for response in (ok, unauthenticated, not_acceptable):
        assert "Allow" not in response
    assert not_allowed.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert not_allowed["Allow"] == "GET, PATCH, HEAD, OPTIONS"
    assert options["Allow"] == "GET, PATCH, HEAD, OPTIONS"


@pytest.mark.django_db
@pytest.mark.parametrize("accept", ["application/json", "text/html"])
def test_a_path_that_is_not_a_route_gets_no_allow(
    user_client: Client, accept: str
) -> None:
    # No route, no methods to list: Django's 404 whatever the Accept, no Allow.
    response = user_client.get("/api/v1/nothing/", headers={"Accept": accept})
    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json() == {"code": "not_found", "detail": "Not Found"}
    assert "Allow" not in response


@pytest.mark.django_db
def test_head_is_served_by_the_get_operation(user_client: Client) -> None:
    get = user_client.get(ME)
    head = user_client.head(ME)
    assert head.status_code == HTTPStatus.OK
    assert head["Content-Type"] == get["Content-Type"]
    assert head.content == b""


def test_the_schema_has_no_head_operations_and_unique_operation_ids() -> None:
    schema = api.get_openapi_schema()
    methods = [m for item in schema["paths"].values() for m in item]
    assert "head" not in methods
    ids = Counter(
        op["operationId"] for item in schema["paths"].values() for op in item.values()
    )
    assert all(count == 1 for count in ids.values()), ids


def test_allow_orders_the_methods_like_django() -> None:
    assert _allow_header({"patch", "get"}) == "GET, PATCH, HEAD, OPTIONS"
    assert _allow_header({"post"}) == "POST, OPTIONS"
    assert _allow_header({"delete", "get", "put", "post"}) == (
        "GET, POST, PUT, DELETE, HEAD, OPTIONS"
    )
    # Django's View.http_method_names order: get, post, put, patch, delete,
    # then head and options.
    assert _allow_header({"delete", "patch", "put", "post", "get"}) == (
        "GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS"
    )


def _api_routes(
    patterns: list[URLPattern | URLResolver], prefix: str = ""
) -> list[tuple[str, str | None]]:
    """(route, url name) of every URL pattern under /api/v1/."""
    found: list[tuple[str, str | None]] = []
    for pattern in patterns:
        route = prefix + str(pattern.pattern)
        if isinstance(pattern, URLResolver):
            found += _api_routes(pattern.url_patterns, route)
        elif route.startswith("api/v1/"):
            found.append((route, pattern.name))
    return found


@pytest.mark.django_db
def test_every_api_route_answers_its_own_json_405(user_client: Client, user) -> None:
    # The 405 is decided before Ninja runs, from the schema. A route the schema
    # does not list (include_in_schema=False) would get Ninja's text 405 here.
    # Ninja's api-root always raises 404 (ninja/openapi/views.py default_home).
    routes = [
        (route, name)
        for route, name in _api_routes(get_resolver().url_patterns)
        if name != "api-root"
    ]
    assert {name for _, name in routes} >= {
        "user-list",
        "user-me",
        "user-detail",
        "openapi-json",
        "openapi-view",
    }
    for route, name in routes:
        path = "/" + route.replace("<public_id:id>", str(user.public_id))
        response = user_client.generic("TRACE", path)
        assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED, name
        assert response["Content-Type"] == "application/json", name
        assert response.json() == {
            "code": "method_not_allowed",
            "detail": "Method not allowed",
        }, name
        assert response["Allow"].endswith("OPTIONS"), name
