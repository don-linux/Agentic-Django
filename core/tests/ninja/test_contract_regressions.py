"""Contract pins beyond test_parity_pins: OPTIONS metadata names, ScopedThrottle
and explicit rates, error-code mapping, and the path lookup that answers 404
before the body is parsed.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Annotated
from typing import Any
from uuid import UUID

import pytest
from allauth.account.models import EmailAddress
from django.http import Http404
from django.http import HttpRequest  # runtime: Ninja signature
from django.http import HttpResponse  # runtime: response argument
from django.test import Client
from ninja import Router
from ninja import Schema
from ninja.testing import TestClient
from pydantic import AfterValidator
from pydantic import ValidationInfo

from apps.users.tests.factories import UserFactory
from core.api.exceptions import _code_and_detail
from core.api.exceptions import _ninja_validation_fields
from core.api.throttling import AnonThrottle
from core.api.throttling import ScopedThrottle
from core.tests.ninja.support import make_api

if TYPE_CHECKING:
    from apps.users.models import User

ME_URL = "/api/v1/users/me/"
LIST_URL = "/api/v1/users/"
PASSWORD = "My_R@ndom-P@ssw0rd"  # noqa: S105
LOGIN_URL = "/_allauth/app/v1/auth/login"


def _token_for(*, is_staff: bool) -> str:
    user = UserFactory(password=PASSWORD, is_staff=is_staff)
    EmailAddress.objects.create(
        user=user, email=user.email, primary=True, verified=True
    )
    response = Client().post(
        LOGIN_URL,
        {"email": user.email, "password": PASSWORD},
        content_type="application/json",
    )
    assert response.status_code == HTTPStatus.OK, response.content
    return response.json()["meta"]["session_token"]


# --- OPTIONS (DRF SimpleMetadata) ---------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("url", "name", "allow"),
    [
        (ME_URL, "Retrieve the current user", "GET, PATCH, HEAD, OPTIONS"),
        (LIST_URL, "List users", "GET, HEAD, OPTIONS"),
    ],
)
def test_authenticated_options_is_drf_metadata(
    user_client: Client, url: str, name: str, allow: str
) -> None:
    response = user_client.options(url)
    assert response.status_code == HTTPStatus.OK
    assert response["Allow"] == allow
    assert response.json() == {
        "name": name,
        "description": "",
        "renders": ["application/json"],
        "parses": ["application/json"],
    }


@pytest.mark.django_db
def test_options_accepts_the_session_token() -> None:
    token = _token_for(is_staff=False)
    response = Client().options(ME_URL, headers={"X-Session-Token": token})
    assert response.status_code == HTTPStatus.OK
    assert response["Allow"] == "GET, PATCH, HEAD, OPTIONS"
    assert response.json() == {
        "name": "Retrieve the current user",
        "description": "",
        "renders": ["application/json"],
        "parses": ["application/json"],
    }
    bogus = Client().options(ME_URL, headers={"X-Session-Token": "bogus"})
    assert bogus.status_code == HTTPStatus.UNAUTHORIZED
    assert bogus.json()["code"] == "not_authenticated"


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
            {"type": "string_too_short", "msg": "x", "ctx": {"min_length": 3}},
            ("min_length", "Ensure this field has at least 3 characters."),
        ),
        ({"type": "too_long", "msg": "List too long"}, ("max_length", "List too long")),
        (
            {"type": "model_attributes_type", "msg": "x", "input_type": "list"},
            ("invalid", "Invalid data. Expected a dictionary, but got list."),
        ),
        (
            {"type": "value_error", "msg": "May not be null"},
            ("null", "May not be null"),
        ),
        ({"type": "custom_rule", "msg": "Custom."}, ("invalid", "Custom.")),
        ({}, ("invalid", "Invalid input.")),
    ],
)
def test_code_and_detail_branches(
    err: dict[str, Any], expected: tuple[str, str]
) -> None:
    assert _code_and_detail(err) == expected


def test_a_leaf_and_a_nested_error_on_the_same_key_keep_the_nested_one() -> None:
    fields = _ninja_validation_fields(
        [
            {"loc": ("body", "body", "address"), "type": "missing", "msg": "m"},
            {"loc": ("body", "body", "address", "city"), "type": "missing", "msg": "m"},
        ]
    )
    assert fields == {
        "address": {"city": [{"code": "required", "detail": "This field is required."}]}
    }


# --- lookup before body: a public AfterValidator on the path argument -------


class _Body(Schema):
    title: str


_seen: list[UUID] = []


def _visible(value: UUID, info: ValidationInfo) -> UUID:
    # The request is in info.context (ninja ParamModel.resolve passes it).
    assert info.context is not None
    assert "request" in info.context
    _seen.append(value)
    if value != UUID(int=1):
        raise Http404
    return value


_VisibleId = Annotated[UUID, AfterValidator(_visible)]


def _lookup_api() -> TestClient:
    _seen.clear()
    api = make_api(throttle=[])
    router = Router()

    @router.patch("/things/{uuid:thing_id}/", auth=None)
    def update(
        request: HttpRequest,
        thing_id: _VisibleId,
        q: int,
        body: _Body,
        response: HttpResponse,
    ) -> dict[str, Any]:
        response["X-Seen"] = "1"
        return {"q": q, "title": body.title}

    api.add_router("/", router)
    return TestClient(api)


def _patch(client: TestClient, path: str, body: object) -> Any:
    # ninja.testing builds a Mock request: content_type must be set explicitly.
    return client.patch(path, json=body, content_type="application/json")


def test_a_missing_object_is_404_before_the_body_is_parsed() -> None:
    client = _lookup_api()
    response = client.patch(
        f"/things/{UUID(int=2)}/?q=1", body="{not json", content_type="text/plain"
    )
    assert response.status_code == HTTPStatus.NOT_FOUND
    assert _seen == [UUID(int=2)]


def test_a_bad_query_parameter_is_400() -> None:
    response = _patch(_lookup_api(), f"/things/{UUID(int=1)}/?q=abc", {"title": "t"})
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["fields"]["q"][0]["code"] == "invalid"


def test_the_response_argument_reaches_the_view() -> None:
    response = _patch(_lookup_api(), f"/things/{UUID(int=1)}/?q=2", {"title": "t"})
    assert response.status_code == HTTPStatus.OK
    assert response["X-Seen"] == "1"
    assert _seen == [UUID(int=1)]
