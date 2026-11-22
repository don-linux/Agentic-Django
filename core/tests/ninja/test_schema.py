"""The schema and the docs are staff-only, by session or by X-Session-Token."""

from __future__ import annotations

import json
from http import HTTPStatus

import pytest
import yaml
from allauth.account.models import EmailAddress
from django.test import Client
from django.test import RequestFactory
from django.urls import reverse

from apps.users.tests.factories import UserFactory
from core.api.negotiation import render_schema
from core.tests.credentials import PASSWORD

LOGIN_URL = "/_allauth/app/v1/auth/login"
NOT_AUTHENTICATED = {
    "code": "not_authenticated",
    "detail": "Authentication credentials were not provided.",
}
PERMISSION_DENIED = {
    "code": "permission_denied",
    "detail": "You do not have permission to perform this action.",
}
UNDECLARED_METHODS = ("POST", "PUT", "PATCH", "DELETE")


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


@pytest.mark.django_db
@pytest.mark.parametrize("route", ["api-schema", "api-docs"])
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
@pytest.mark.parametrize("route", ["api-schema", "api-docs"])
def test_the_route_is_staff_only_by_x_session_token(route: str) -> None:
    url = reverse(route)

    staff = Client().get(url, headers={"X-Session-Token": _token_for(is_staff=True)})
    assert staff.status_code == HTTPStatus.OK

    not_staff = Client().get(
        url, headers={"X-Session-Token": _token_for(is_staff=False)}
    )
    assert not_staff.status_code == HTTPStatus.FORBIDDEN
    assert not_staff.json() == PERMISSION_DENIED

    bogus = Client().get(url, headers={"X-Session-Token": "bogus"})
    assert bogus.status_code == HTTPStatus.UNAUTHORIZED
    assert bogus["WWW-Authenticate"] == "X-Session-Token"
    assert bogus.json() == NOT_AUTHENTICATED


@pytest.mark.django_db
@pytest.mark.parametrize("route", ["api-schema", "api-docs"])
def test_an_undeclared_method_is_405_before_authentication(
    client: Client, user_client: Client, admin_client: Client, route: str
) -> None:
    # docs/api.md: 405 comes before authentication. The view would answer an
    # anonymous 401 and a non-staff 403, as DRF did, if the method got that far.
    url = reverse(route)
    callers = {"anonymous": client, "user": user_client, "staff": admin_client}
    for method in UNDECLARED_METHODS:
        for who, caller in callers.items():
            response = caller.generic(method, url)
            assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED, (method, who)
            assert response["Allow"] == "GET, HEAD, OPTIONS", (method, who)
            assert response.json() == {
                "code": "method_not_allowed",
                "detail": f'Method "{method}" not allowed.',
            }, (method, who)


@pytest.mark.django_db
def test_the_docs_take_a_text_wildcard_accept(admin_client: Client) -> None:
    url = reverse("api-docs")
    wildcard = admin_client.get(url, headers={"Accept": "text/*"})
    assert wildcard.status_code == HTTPStatus.OK
    assert wildcard["Content-Type"].startswith("text/html")
    other_type = admin_client.get(url, headers={"Accept": "application/*"})
    assert other_type.status_code == HTTPStatus.NOT_ACCEPTABLE
    assert other_type.json()["code"] == "not_acceptable"


SCHEMA_YAML = "application/vnd.oai.openapi"
SCHEMA_JSON = "application/vnd.oai.openapi+json"
JSON = "application/json"
NOT_ACCEPTABLE = {
    "code": "not_acceptable",
    "detail": "Could not satisfy the request Accept header.",
}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "accept", ["text/plain", "text/html", "text/*", "text/yaml", "application/xml"]
)
def test_the_schema_refuses_an_accept_it_cannot_produce(
    client: Client, user_client: Client, admin_client: Client, accept: str
) -> None:
    # SpectacularAPIView rendered application/vnd.oai.openapi, application/yaml,
    # application/vnd.oai.openapi+json and application/json; DRF negotiated
    # them before authentication, so the 406 comes first for everyone.
    url = reverse("api-schema")
    for caller in (client, user_client, admin_client):
        response = caller.get(url, headers={"Accept": accept})
        assert response.status_code == HTTPStatus.NOT_ACCEPTABLE, accept
        assert response.json() == NOT_ACCEPTABLE, accept


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["HEAD", "OPTIONS", "POST"])
def test_the_schema_406_comes_before_the_method_checks(
    admin_client: Client, method: str
) -> None:
    # As for the docs: DRF negotiated before the handler and the 405.
    response = admin_client.generic(
        method, reverse("api-schema"), headers={"Accept": "text/plain"}
    )
    assert response.status_code == HTTPStatus.NOT_ACCEPTABLE


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("accept", "media_type"),
    [
        (None, SCHEMA_YAML),
        ("*/*", SCHEMA_YAML),
        ("application/*", SCHEMA_YAML),
        ("application/vnd.oai.openapi", SCHEMA_YAML),
        ("application/yaml", "application/yaml"),
        ("text/html, */*;q=0.8", SCHEMA_YAML),
        ("application/vnd.oai.openapi+json", SCHEMA_JSON),
        ("application/json", JSON),
        ("text/plain, application/json", JSON),
        ("application/json, text/yaml", JSON),
    ],
)
def test_the_schema_serves_each_media_type_it_offers(
    admin_client: Client, accept: str | None, media_type: str
) -> None:
    # The negative case: an Accept that names (or covers) one of the four media
    # types is still 200 in its format.
    headers = {} if accept is None else {"Accept": accept}
    response = admin_client.get(reverse("api-schema"), headers=headers)
    assert response.status_code == HTTPStatus.OK, accept
    assert response["Content-Type"].split(";")[0] == media_type, accept
    loads = json.loads if media_type in {SCHEMA_JSON, JSON} else yaml.safe_load
    assert "openapi" in loads(response.content), accept


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("accept", "content_type", "indent"),
    [
        (None, f"{SCHEMA_YAML}; charset=utf-8", None),
        ("application/yaml", "application/yaml; charset=utf-8", None),
        ("application/json", "application/json", 4),
        (SCHEMA_JSON, SCHEMA_JSON, 4),
        ("application/json; indent=2", "application/json", 2),
        (f"{SCHEMA_JSON}; indent=1", SCHEMA_JSON, 1),
        ("application/json; indent=0", "application/json", 4),
        ("application/json; indent=9", "application/json", 8),
        ("application/json; indent=wide", "application/json", 4),
    ],
)
def test_the_schema_is_rendered_as_spectacular_rendered_it(
    admin_client: Client, accept: str | None, content_type: str, indent: int | None
) -> None:
    # drf-spectacular 0.30.0: each of the four renderers answers with its own
    # media type; the JSON ones are DRF's JSONRenderer (no charset) indented by
    # the Accept's indent parameter (0-8) or 4, the YAML ones carry utf-8.
    headers = {} if accept is None else {"Accept": accept}
    response = admin_client.get(reverse("api-schema"), headers=headers)
    assert response.status_code == HTTPStatus.OK, accept
    assert response["Content-Type"] == content_type, accept
    if indent is None:
        assert response.content.startswith(b"openapi:"), accept
    else:
        assert response.content.startswith(b"{\n" + b" " * indent + b'"openapi"')


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("accept", "content_type"),
    [
        # A media type with a parameter is more specific than one without, so
        # it wins over the earlier YAML renderer (DRF's precedence) ...
        ("application/json;version=2, application/yaml", "application/json"),
        ("application/*, application/json", "application/json"),
        # ... and at one precedence the renderer order decides, not the client's
        # (q is not a parameter and is ignored).
        ("application/json, application/yaml", "application/yaml; charset=utf-8"),
        (
            "application/json;q=1, application/yaml;q=0.1",
            "application/yaml; charset=utf-8",
        ),
        # Only q is no parameter either: the YAML renderer still comes first.
        ("application/json;q=0.5, application/yaml", "application/yaml; charset=utf-8"),
        ("application/json;, application/yaml", "application/yaml; charset=utf-8"),
        (f"application/json, {SCHEMA_JSON}", SCHEMA_JSON),
    ],
)
def test_the_schema_renderer_is_chosen_as_drf_chose_it(
    admin_client: Client, accept: str, content_type: str
) -> None:
    response = admin_client.get(reverse("api-schema"), headers={"Accept": accept})
    assert response.status_code == HTTPStatus.OK, accept
    assert response["Content-Type"] == content_type, accept


def test_the_schema_json_and_yaml_keep_spectacular_details() -> None:
    # DRF's JSONRenderer: UTF-8 as is, U+2028/U+2029 escaped; OpenApiYamlRenderer:
    # a multi-line string is a | block, a one-line string stays plain.
    data = {"title": "Café\u2028", "description": "one\ntwo", "summary": "one"}
    factory = RequestFactory()
    as_json = render_schema(factory.get("/", headers={"Accept": JSON}), data)
    expected = (
        '{\n    "title": "Café\\u2028",\n    "description": "one\\ntwo",\n'
        '    "summary": "one"\n}'
    )
    assert as_json.content == expected.encode()
    as_yaml = render_schema(factory.get("/"), data).content.decode()
    assert as_yaml.endswith("description: |-\n  one\n  two\nsummary: one\n")


def test_the_schema_view_refuses_on_its_own_what_it_cannot_render() -> None:
    # ApiContractMiddleware answers this 406 first; the view does not depend on it.
    request = RequestFactory().get("/", headers={"Accept": "text/plain"})
    response = render_schema(request, {"openapi": "3.1.0"})
    assert response.status_code == HTTPStatus.NOT_ACCEPTABLE
    assert json.loads(response.content) == NOT_ACCEPTABLE
