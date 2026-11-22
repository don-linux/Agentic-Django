"""Users operations in the generated OpenAPI 3.1 document."""

from __future__ import annotations

from http import HTTPStatus

from django.urls import reverse

from core.api.api import api

# User.name is CharField(max_length=255) (apps/users/models.py).
NAME_MAX_LENGTH = 255


def _schema() -> dict:
    return api.get_openapi_schema()


def test_api_schema_generated_successfully(admin_client) -> None:
    url = reverse("api-schema")
    response = admin_client.get(url)
    assert response.status_code == HTTPStatus.OK
    # The runtime schema view still defaults to YAML for the HTTP contract.
    assert response["Content-Type"].startswith(
        ("application/yaml", "application/vnd.oai.openapi")
    )


def test_users_paths_are_documented() -> None:
    paths = _schema()["paths"]
    assert "/api/v1/users/" in paths
    assert "/api/v1/users/me/" in paths
    assert "/api/v1/users/{id}/" in paths


def test_security_schemes_match_the_published_contract() -> None:
    schemes = _schema()["components"]["securitySchemes"]
    assert schemes["XSessionTokenAuth"]["in"] == "header"
    assert schemes["XSessionTokenAuth"]["name"] == "X-Session-Token"
    assert schemes["cookieAuth"]["in"] == "cookie"
    assert schemes["cookieAuth"]["name"] == "sessionid"


def test_user_out_documents_name_length_and_url_format() -> None:
    user = _schema()["components"]["schemas"]["UserOut"]
    assert user["properties"]["name"]["maxLength"] == NAME_MAX_LENGTH
    assert user["properties"]["url"]["format"] == "uri"


def test_patch_body_is_published_with_additional_properties_false() -> None:
    body = _schema()["components"]["schemas"]["UserUpdateIn"]
    assert body["additionalProperties"] is False
    assert "name" in body["properties"]


def test_list_page_parameter_is_integer() -> None:
    params = _schema()["paths"]["/api/v1/users/"]["get"]["parameters"]
    page = next(p for p in params if p["name"] == "page")
    schema = page["schema"]
    types = schema.get("anyOf") or [schema]
    assert any(item.get("type") == "integer" for item in types)
