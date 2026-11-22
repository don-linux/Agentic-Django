"""Users operations in the generated OpenAPI 3.1 document."""

from __future__ import annotations

from http import HTTPStatus

from django.urls import reverse

from core.api.api import api

# User.name is CharField(max_length=255) (apps/users/models.py).
NAME_MAX_LENGTH = 255

_UUID_PATTERN = (
    "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _schema() -> dict:
    return api.get_openapi_schema()


def _codes(responses: dict) -> set[str]:
    return {str(code) for code in responses}


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


def test_schema_has_error_components() -> None:
    schemas = _schema()["components"]["schemas"]
    assert "ErrorSchema" in schemas
    assert "ValidationErrorSchema" in schemas
    assert set(schemas["ErrorSchema"]["required"]) == {"code", "detail"}


def test_security_schemes_match_the_published_contract() -> None:
    schemes = _schema()["components"]["securitySchemes"]
    assert schemes["XSessionTokenAuth"]["in"] == "header"
    assert schemes["XSessionTokenAuth"]["name"] == "X-Session-Token"
    assert schemes["cookieAuth"]["in"] == "cookie"
    assert schemes["cookieAuth"]["name"] == "sessionid"


def test_cookie_auth_documents_both_cookie_names() -> None:
    cookie = _schema()["components"]["securitySchemes"]["cookieAuth"]
    description = cookie["description"]
    assert "sessionid" in description
    assert "__Secure-sessionid" in description


def test_user_out_documents_name_length_and_url_format() -> None:
    user = _schema()["components"]["schemas"]["UserOut"]
    assert user["properties"]["name"]["maxLength"] == NAME_MAX_LENGTH
    assert user["properties"]["url"]["format"] == "uri"


def test_patch_body_is_published_with_additional_properties_false() -> None:
    body = _schema()["components"]["schemas"]["UserUpdateIn"]
    assert body["additionalProperties"] is False
    assert "name" in body["properties"]


def test_request_components_leave_out_read_only_fields() -> None:
    body = _schema()["components"]["schemas"]["UserUpdateIn"]
    assert "id" not in body["properties"]
    assert "url" not in body["properties"]


def test_list_page_parameter_is_integer() -> None:
    params = _schema()["paths"]["/api/v1/users/"]["get"]["parameters"]
    page = next(p for p in params if p["name"] == "page")
    schema = page["schema"]
    types = schema.get("anyOf") or [schema]
    assert any(item.get("type") == "integer" for item in types)


def test_path_parameter_is_id_with_uuid_format() -> None:
    # Ninja emits a string + UUID pattern rather than format: uuid (alignment).
    params = _schema()["paths"]["/api/v1/users/{id}/"]["get"]["parameters"]
    ident = next(p for p in params if p["in"] == "path")
    assert ident["name"] == "id"
    assert ident["schema"]["type"] == "string"
    assert ident["schema"]["pattern"] == _UUID_PATTERN


def test_users_expose_patch_but_not_put() -> None:
    for path in ("/api/v1/users/me/", "/api/v1/users/{id}/"):
        methods = set(_schema()["paths"][path])
        assert "patch" in methods
        assert "put" not in methods
        assert "delete" not in methods


def test_schema_does_not_describe_itself() -> None:
    paths = _schema()["paths"]
    assert [path for path in paths if "schema" in path or "docs" in path] == []


def test_me_documents_401_but_not_404_or_400() -> None:
    # /me/ has no lookup and GET has no body: 404 and 400 stay off the GET op.
    responses = _codes(_schema()["paths"]["/api/v1/users/me/"]["get"]["responses"])
    assert "401" in responses
    assert "404" not in responses
    assert "400" not in responses


def test_user_update_documents_error_responses() -> None:
    patch = _schema()["paths"]["/api/v1/users/{id}/"]["patch"]["responses"]
    assert {"200", "400", "401", "404"} <= _codes(patch)
    body = patch.get("400") or patch[400]
    one_of = body["content"]["application/json"]["schema"]["oneOf"]
    assert one_of == [
        {"$ref": "#/components/schemas/ValidationErrorSchema"},
        {"$ref": "#/components/schemas/ErrorSchema"},
    ]


def test_tags_and_operation_ids_ignore_the_version_prefix() -> None:
    for path, operations in _schema()["paths"].items():
        for method, operation in operations.items():
            if method not in {"get", "patch", "post", "put", "delete"}:
                continue
            tags = operation.get("tags") or []
            assert all(not tag.startswith("v") for tag in tags), (path, method, tags)
            op_id = operation.get("operationId", "")
            assert "v1" not in op_id, (path, method, op_id)


def test_request_bodies_are_json_except_file_uploads() -> None:
    for path, operations in _schema()["paths"].items():
        for method, operation in operations.items():
            body = operation.get("requestBody")
            if not body:
                continue
            content = set(body.get("content", {}))
            assert content == {"application/json"}, (path, method, content)
