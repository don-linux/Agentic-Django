"""Contract pins beyond test_parity_pins: OPTIONS metadata names, staff gate
on OPTIONS, ScopedThrottle and explicit rates, error-code mapping, and Ninja
validating the whole request before the view looks an object up.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Any
from uuid import UUID

import pytest
import yaml
from allauth.account.models import EmailAddress
from django.http import Http404
from django.http import HttpRequest  # runtime: Ninja signature
from django.http import HttpResponse  # runtime: response argument
from django.test import Client
from ninja import Router
from ninja import Schema
from ninja.testing import TestClient

from apps.users.tests.factories import UserFactory
from core.api.exceptions import _code_and_detail
from core.api.exceptions import _ninja_validation_fields
from core.api.negotiation import _parses
from core.api.throttling import AnonThrottle
from core.api.throttling import ScopedThrottle
from core.tests.credentials import PASSWORD
from core.tests.ninja.support import make_api

if TYPE_CHECKING:
    from apps.users.models import User

ME_URL = "/api/v1/users/me/"
LIST_URL = "/api/v1/users/"
DOCS_URL = "/api/v1/docs/"
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


def test_a_leaf_and_a_nested_error_on_the_same_key_keep_the_nested_one() -> None:
    fields = _ninja_validation_fields(
        [
            {"loc": ("body", "body", "address"), "type": "missing", "msg": "m"},
            {"loc": ("body", "body", "address", "city"), "type": "missing", "msg": "m"},
        ]
    )
    assert fields == {"address": {"city": [{"code": "missing", "detail": "m"}]}}


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


# --- pagination and schema helpers --------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("url", [DOCS_URL, "/api/v1/schema/"])
def test_options_on_staff_docs_is_not_available_to_a_regular_user(
    user_client: Client, url: str
) -> None:
    response = user_client.options(url)
    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.json()["code"] == "permission_denied"


@pytest.mark.django_db
@pytest.mark.parametrize("url", [DOCS_URL, "/api/v1/schema/"])
def test_options_on_staff_docs_allows_staff(admin_client: Client, url: str) -> None:
    response = admin_client.options(url)
    assert response.status_code == HTTPStatus.OK
    assert response["Allow"] == "GET, HEAD, OPTIONS"


# SpectacularAPIView's OPTIONS in the previous contract (DRF SimpleMetadata).
SCHEMA_OPTIONS_METADATA = {
    "name": "Schema",
    "description": "Staff-only OpenAPI schema; errors stay application/json.",
    "renders": [
        "application/vnd.oai.openapi",
        "application/yaml",
        "application/vnd.oai.openapi+json",
        "application/json",
    ],
    "parses": ["application/json"],
}


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("accept", "content_type"),
    [
        (None, "application/vnd.oai.openapi; charset=utf-8"),
        ("application/yaml", "application/yaml; charset=utf-8"),
        ("application/json", "application/json"),
        ("application/vnd.oai.openapi+json", "application/vnd.oai.openapi+json"),
    ],
)
def test_options_on_the_schema_answers_its_metadata_in_the_schema_format(
    admin_client: Client, accept: str | None, content_type: str
) -> None:
    # As SpectacularAPIView: the metadata goes through the renderer the Accept
    # chose, so it is YAML by default and indented JSON on request.
    headers = {} if accept is None else {"Accept": accept}
    response = admin_client.options("/api/v1/schema/", headers=headers)
    assert response.status_code == HTTPStatus.OK
    assert response["Content-Type"] == content_type
    assert response["Allow"] == "GET, HEAD, OPTIONS"
    if content_type.endswith("json"):
        assert response.content.startswith(b'{\n    "name": "Schema"')
        assert json.loads(response.content) == SCHEMA_OPTIONS_METADATA
    else:
        assert response.content.startswith(b"name: Schema\n")
        assert yaml.safe_load(response.content) == SCHEMA_OPTIONS_METADATA


@pytest.mark.django_db
def test_options_on_the_docs_keeps_its_json_metadata(admin_client: Client) -> None:
    # The negative case: the docs page is not the schema. The previous contract
    # failed there with a 500; this answer is the API's JSON metadata.
    response = admin_client.options(DOCS_URL)
    assert response.status_code == HTTPStatus.OK
    assert response["Content-Type"] == "application/json"
    assert response.json() == {
        "name": "api-docs",
        "description": "",
        "renders": ["application/json"],
        "parses": ["application/json"],
    }


@pytest.mark.django_db
@pytest.mark.parametrize("url", [DOCS_URL, "/api/v1/schema/"])
@pytest.mark.parametrize(
    ("who", "status", "code"),
    [
        ("client", HTTPStatus.UNAUTHORIZED, "not_authenticated"),
        ("user_client", HTTPStatus.FORBIDDEN, "permission_denied"),
    ],
)
def test_options_refused_by_the_staff_gate_still_has_allow(
    request: pytest.FixtureRequest, url: str, who: str, status: int, code: str
) -> None:
    # DRF's finalize_response set Allow on every response of the view, the
    # 401/403 of the permission check included.
    response = request.getfixturevalue(who).options(url)
    assert response.status_code == status
    assert response.json()["code"] == code
    assert response["Allow"] == "GET, HEAD, OPTIONS"


@pytest.mark.django_db
@pytest.mark.parametrize("url", [DOCS_URL, "/api/v1/schema/"])
def test_the_staff_gate_refusal_on_options_carries_no_metadata(
    client: Client, user_client: Client, url: str
) -> None:
    # The negative case: Allow is the only thing the refusal shares with the
    # staff answer; the metadata body stays behind the gate.
    for caller in (client, user_client):
        body = caller.options(url).json()
        assert set(body) == {"code", "detail"}


def test_options_parses_lists_each_request_body_media_type() -> None:
    # An upload operation (ninja File/Form) takes multipart/form-data; OPTIONS
    # must not advertise JSON only (DRF listed the view's parser classes).
    upload: dict[str, Any] = {
        "summary": "Upload a file",
        "requestBody": {"content": {"multipart/form-data": {"schema": {}}}},
    }
    patch: dict[str, Any] = {
        "requestBody": {"content": {"application/json": {"schema": {}}}}
    }
    assert _parses({"POST": upload}) == ["multipart/form-data"]
    assert _parses({"PATCH": patch, "POST": upload}) == [
        "application/json",
        "multipart/form-data",
    ]
    # A route without a request body keeps DRF's default JSON parser.
    assert _parses({"GET": {"summary": "List users"}}) == ["application/json"]
