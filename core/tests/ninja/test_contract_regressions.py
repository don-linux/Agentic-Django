"""Contract pins: OPTIONS as Django answers it,
ScopedThrottle and explicit rates, error-code mapping, and Ninja validating the
whole request before the view looks an object up.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Any
from uuid import UUID

import pytest
from django.http import Http404
from django.http import HttpRequest  # runtime: Ninja signature
from django.http import HttpResponse  # runtime: response argument
from ninja import Router
from ninja import Schema
from ninja.testing import TestClient

from core.api.exceptions import _code_and_detail
from core.api.exceptions import _ninja_validation_fields
from core.api.throttling import AnonThrottle
from core.api.throttling import ScopedThrottle
from core.tests.ninja.support import make_api

if TYPE_CHECKING:
    from apps.users.models import User

ME_URL = "/api/v1/users/me/"
LIST_URL = "/api/v1/users/"
DOCS_URL = "/api/v1/docs/"

# --- OPTIONS (Django's View.options) -----------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("url", "allow"),
    [
        (ME_URL, "GET, PATCH, HEAD, OPTIONS"),
        (LIST_URL, "GET, HEAD, OPTIONS"),
        (DOCS_URL, "GET, HEAD, OPTIONS"),
        ("/api/v1/schema/", "GET, HEAD, OPTIONS"),
    ],
)
@pytest.mark.parametrize("who", ["client", "user_client", "admin_client"])
def test_options_lists_the_methods_with_no_body_for_anyone(
    request: pytest.FixtureRequest, url: str, allow: str, who: str
) -> None:
    # As Django's View.options(): 200, Allow, Content-Length 0, and no
    # authentication, on the staff-only docs and schema too.
    response = request.getfixturevalue(who).options(url)
    assert response.status_code == HTTPStatus.OK
    assert response["Allow"] == allow
    assert response["Content-Length"] == "0"
    assert response.content == b""


# --- ScopedThrottle -----------------------------------------------------------


def _scoped_api(scope: str):
    api = make_api(throttle=[])
    router = Router()

    @router.get("/scoped/", auth=None, throttle=[ScopedThrottle("2/min", scope=scope)])
    def scoped(request: HttpRequest) -> dict[str, bool]:
        return {"ok": True}

    api.add_router("/", router)
    return TestClient(api)


@pytest.mark.django_db
def test_scoped_throttle_is_429_after_the_rate(user: User) -> None:
    client = _scoped_api("g-scope-ip")
    ip = {"REMOTE_ADDR": "198.51.100.21"}
    for _ in range(2):
        assert client.get("/scoped/", META=ip).status_code == HTTPStatus.OK
    blocked = client.get("/scoped/", META=ip)
    assert blocked.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert int(blocked["Retry-After"]) >= 1
    # Another client IP has its own bucket.
    other = client.get("/scoped/", META={"REMOTE_ADDR": "198.51.100.22"})
    assert other.status_code == HTTPStatus.OK


@pytest.mark.django_db
def test_scoped_throttle_keys_a_user_by_pk_not_by_ip(user: User) -> None:
    client = _scoped_api("g-scope-user")
    for n in range(2):
        meta = {"REMOTE_ADDR": f"198.51.100.{30 + n}"}
        assert client.get("/scoped/", user=user, META=meta).status_code == HTTPStatus.OK
    moved = client.get("/scoped/", user=user, META={"REMOTE_ADDR": "198.51.100.40"})
    assert moved.status_code == HTTPStatus.TOO_MANY_REQUESTS


def test_an_explicit_rate_is_not_replaced_by_the_settings_rate(rf) -> None:
    # Ninja's documented idiom: AnonRateThrottle("10/s"). The settings "anon"
    # rate must not override it on each request.
    throttle = AnonThrottle("1/hour")
    request = rf.get("/", REMOTE_ADDR="203.0.113.77")
    request.auth = None
    assert throttle.allow_request(request) is True
    assert throttle.allow_request(request) is False


# --- error mapping ------------------------------------------------------------


@pytest.mark.parametrize(
    ("err", "expected"),
    [
        (
            {"type": "extra_forbidden", "msg": "Extra inputs are not permitted"},
            ("unknown_field", "Extra inputs are not permitted"),
        ),
        (
            {"type": "string_too_short", "msg": "String should have at least 3"},
            ("string_too_short", "String should have at least 3"),
        ),
        ({"type": "custom_rule", "msg": "Custom."}, ("custom_rule", "Custom.")),
    ],
)
def test_a_field_error_keeps_pydantic_s_type_except_unknown_field(
    err: dict[str, Any], expected: tuple[str, str]
) -> None:
    assert _code_and_detail(err) == expected


def test_a_leaf_and_a_nested_error_on_the_same_key_keep_both() -> None:
    # The key's own error goes under its non_field_errors; none is dropped.
    fields = _ninja_validation_fields(
        [
            {"loc": ("body", "body", "address"), "type": "missing", "msg": "m"},
            {"loc": ("body", "body", "address", "city"), "type": "missing", "msg": "m"},
        ]
    )
    assert fields == {
        "address": {
            "non_field_errors": [{"code": "missing", "detail": "m"}],
            "city": [{"code": "missing", "detail": "m"}],
        }
    }


# --- the whole request is validated before the view looks anything up -------


class _Body(Schema):
    title: str


_seen: list[UUID] = []


def _lookup_api() -> TestClient:
    _seen.clear()
    api = make_api(throttle=[])
    router = Router()

    @router.patch("/things/{uuid:thing_id}/", auth=None)
    def update(
        request: HttpRequest,
        thing_id: UUID,
        q: int,
        body: _Body,
        response: HttpResponse,
    ) -> dict[str, Any]:
        # The view's own lookup, as apps/users/api/views.py does.
        _seen.append(thing_id)
        if thing_id != UUID(int=1):
            raise Http404
        response["X-Seen"] = "1"
        return {"q": q, "title": body.title}

    api.add_router("/", router)
    return TestClient(api)


def _patch(client: TestClient, path: str, body: object) -> Any:
    # ninja.testing builds a Mock request: content_type must be set explicitly.
    return client.patch(path, json=body, content_type="application/json")


def test_an_invalid_body_is_400_before_the_view_looks_the_object_up() -> None:
    # Ninja validates path, query and body together before calling the view.
    response = _patch(_lookup_api(), f"/things/{UUID(int=2)}/?q=1", {"title": 1})
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert _seen == []


def test_a_valid_request_for_a_missing_object_is_404() -> None:
    response = _patch(_lookup_api(), f"/things/{UUID(int=2)}/?q=1", {"title": "t"})
    assert response.status_code == HTTPStatus.NOT_FOUND
    assert _seen == [UUID(int=2)]


def test_a_bad_query_parameter_is_400() -> None:
    response = _patch(_lookup_api(), f"/things/{UUID(int=1)}/?q=abc", {"title": "t"})
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["fields"]["q"][0]["code"] == "int_parsing"


def test_the_response_argument_reaches_the_view() -> None:
    response = _patch(_lookup_api(), f"/things/{UUID(int=1)}/?q=2", {"title": "t"})
    assert response.status_code == HTTPStatus.OK
    assert response["X-Seen"] == "1"
    assert _seen == [UUID(int=1)]
