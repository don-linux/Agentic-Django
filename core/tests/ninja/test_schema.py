"""The schema and the docs are Ninja's own views, staff-only by session or by
X-Session-Token through NinjaAPI(docs_decorator=...) (ADR 0019)."""

from __future__ import annotations

import json
from http import HTTPStatus
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse

from core.tests.accounts import session_token

NOT_AUTHENTICATED = {
    "code": "not_authenticated",
    "detail": "Unauthorized",
}
PERMISSION_DENIED = {
    "code": "permission_denied",
    "detail": "Forbidden",
}
UNDECLARED_METHODS = ("POST", "PUT", "PATCH", "DELETE")
# Ninja's URL names for the schema and the docs (ninja/openapi/urls.py).
SCHEMA = "api:openapi-json"
DOCS = "api:openapi-view"


@pytest.mark.django_db
@pytest.mark.parametrize("route", [SCHEMA, DOCS])
def test_the_route_is_staff_only_by_session(
    client: Client, user_client: Client, admin_client: Client, route: str
) -> None:
    url = reverse(route)

    anonymous = client.get(url)
    assert anonymous.status_code == HTTPStatus.UNAUTHORIZED
    assert anonymous["WWW-Authenticate"] == "X-Session-Token"
    assert anonymous.json() == NOT_AUTHENTICATED

    not_staff = user_client.get(url)
    assert not_staff.status_code == HTTPStatus.FORBIDDEN
    assert not_staff.json() == PERMISSION_DENIED

    assert admin_client.get(url).status_code == HTTPStatus.OK


@pytest.mark.django_db
@pytest.mark.parametrize("route", [SCHEMA, DOCS])
def test_the_route_is_staff_only_by_x_session_token(route: str) -> None:
    url = reverse(route)

    staff_token = session_token("staff@example.com", is_staff=True)
    staff = Client().get(url, headers={"X-Session-Token": staff_token})
    assert staff.status_code == HTTPStatus.OK

    member_token = session_token("member@example.com")
    not_staff = Client().get(url, headers={"X-Session-Token": member_token})
    assert not_staff.status_code == HTTPStatus.FORBIDDEN
    assert not_staff.json() == PERMISSION_DENIED

    bogus = Client().get(url, headers={"X-Session-Token": "bogus"})
    assert bogus.status_code == HTTPStatus.UNAUTHORIZED
    assert bogus["WWW-Authenticate"] == "X-Session-Token"
    assert bogus.json() == NOT_AUTHENTICATED


@pytest.mark.django_db
@pytest.mark.parametrize("route", [SCHEMA, DOCS])
def test_an_undeclared_method_is_405_before_authentication(
    client: Client, user_client: Client, admin_client: Client, route: str
) -> None:
    # docs/api.md: 405 comes before authentication. The view would answer an
    # anonymous 401 and a non-staff 403 if the method got that far.
    url = reverse(route)
    callers = {"anonymous": client, "user": user_client, "staff": admin_client}
    for method in UNDECLARED_METHODS:
        for who, caller in callers.items():
            response = caller.generic(method, url)
            assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED, (method, who)
            assert response["Allow"] == "GET, HEAD, OPTIONS", (method, who)
            assert response.json() == {
                "code": "method_not_allowed",
                "detail": "Method not allowed",
            }, (method, who)


@pytest.mark.django_db
def test_the_docs_take_a_text_wildcard_accept(admin_client: Client) -> None:
    url = reverse(DOCS)
    wildcard = admin_client.get(url, headers={"Accept": "text/*"})
    assert wildcard.status_code == HTTPStatus.OK
    assert wildcard["Content-Type"].startswith("text/html")
    other_type = admin_client.get(url, headers={"Accept": "application/*"})
    assert other_type.status_code == HTTPStatus.NOT_ACCEPTABLE
    assert other_type.json()["code"] == "not_acceptable"


NOT_ACCEPTABLE = {
    "code": "not_acceptable",
    "detail": "Could not satisfy the request Accept header.",
}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "accept",
    [
        "text/plain",
        "text/html",
        "text/*",
        "text/yaml",
        "application/xml",
        "application/yaml",
        "application/vnd.oai.openapi",
    ],
)
def test_the_schema_refuses_an_accept_it_cannot_produce(
    client: Client, user_client: Client, admin_client: Client, accept: str
) -> None:
    # Ninja serves the schema as JSON only, and the 406 comes before the staff
    # check, for everyone.
    url = reverse(SCHEMA)
    for caller in (client, user_client, admin_client):
        response = caller.get(url, headers={"Accept": accept})
        assert response.status_code == HTTPStatus.NOT_ACCEPTABLE, accept
        assert response.json() == NOT_ACCEPTABLE, accept


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("method", "status"),
    [
        ("POST", HTTPStatus.METHOD_NOT_ALLOWED),
        ("OPTIONS", HTTPStatus.OK),
        ("HEAD", HTTPStatus.NOT_ACCEPTABLE),
    ],
)
def test_the_schema_405_and_options_come_before_the_406(
    admin_client: Client, method: str, status: int
) -> None:
    response = admin_client.generic(
        method, reverse(SCHEMA), headers={"Accept": "text/plain"}
    )
    assert response.status_code == status


@pytest.mark.django_db
@pytest.mark.parametrize(
    "accept",
    [None, "*/*", "application/*", "application/json", "text/plain, application/json"],
)
def test_the_schema_is_the_committed_contract_as_json(
    admin_client: Client, accept: str | None
) -> None:
    # Ninja's openapi_json view: the same document as docs/openapi.json.
    headers = {} if accept is None else {"Accept": accept}
    response = admin_client.get(reverse(SCHEMA), headers=headers)
    committed = Path(settings.BASE_DIR, "docs", "openapi.json").read_text()
    assert response.status_code == HTTPStatus.OK, accept
    assert response["Content-Type"] == "application/json", accept
    assert response.json() == json.loads(committed), accept
