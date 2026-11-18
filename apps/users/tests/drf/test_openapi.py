from __future__ import annotations

from http import HTTPStatus

import pytest
from django.urls import reverse
from drf_spectacular.generators import SchemaGenerator


def test_api_schema_generated_successfully(admin_client) -> None:
    url = reverse("api-schema")
    response = admin_client.get(url, HTTP_ACCEPT="application/json")
    assert response.status_code == HTTPStatus.OK
    assert response.headers["Content-Type"].startswith("application/json")
    schema = response.json()
    assert "/api/v1/users/me/" in schema["paths"]


ERROR_REF = "#/components/schemas/Error"
VALIDATION_ERROR_REF = "#/components/schemas/ValidationError"


def _response_schema(operation: dict, status: str) -> dict:
    return operation["responses"][status]["content"]["application/json"]["schema"]


def _response_ref(operation: dict, status: str) -> str:
    return _response_schema(operation, status)["$ref"]


@pytest.fixture(scope="module")
def schema() -> dict:
    return SchemaGenerator().get_schema(request=None, public=True)


def test_schema_has_error_components(schema: dict) -> None:
    components = schema["components"]["schemas"]

    assert {"Error", "FieldError", "ValidationError"} <= set(components)
    assert components["Error"]["required"] == ["code", "detail"]
    assert components["ValidationError"]["required"] == ["code", "detail", "fields"]


def test_user_update_documents_error_responses(schema: dict) -> None:
    patch = schema["paths"]["/api/v1/users/{id}/"]["patch"]

    assert _response_schema(patch, "400")["oneOf"] == [
        {"$ref": VALIDATION_ERROR_REF},
        {"$ref": ERROR_REF},
    ]
    for status in ("401", "403", "404", "406", "415", "429"):
        assert _response_ref(patch, status) == ERROR_REF
    assert "WWW-Authenticate" in patch["responses"]["401"]["headers"]
    assert "Retry-After" in patch["responses"]["429"]["headers"]


def test_me_documents_401_but_not_404_or_400(schema: dict) -> None:
    me = schema["paths"]["/api/v1/users/me/"]["get"]

    assert _response_ref(me, "401") == ERROR_REF
    assert "404" not in me["responses"]
    assert "400" not in me["responses"]


def test_user_viewset_exposes_patch_but_not_put(schema: dict) -> None:
    detail = schema["paths"]["/api/v1/users/{id}/"]
    me = schema["paths"]["/api/v1/users/me/"]

    assert "put" not in detail
    assert "patch" in detail
    assert me["patch"]["operationId"] == "users_me_partial_update"
    assert _response_schema(me["patch"], "400")["oneOf"] == [
        {"$ref": VALIDATION_ERROR_REF},
        {"$ref": ERROR_REF},
    ]


def test_schema_does_not_describe_itself(schema: dict) -> None:
    assert "/api/v1/schema/" not in schema["paths"]
    assert "/api/v1/docs/" not in schema["paths"]


def test_request_components_leave_out_read_only_fields(schema: dict) -> None:
    components = schema["components"]["schemas"]
    request_fields = set(components["PatchedUserRequest"]["properties"])

    assert "name" in request_fields
    assert not {"id", "url"} & request_fields
    assert {"id", "name", "url"} <= set(components["User"]["properties"])


def test_tags_and_operation_ids_ignore_the_version_prefix(schema: dict) -> None:
    me = schema["paths"]["/api/v1/users/me/"]["get"]

    assert me["tags"] == ["users"]
    assert me["operationId"] == "users_me_retrieve"


def test_request_bodies_are_json_except_file_uploads(schema: dict) -> None:
    # DEFAULT_PARSER_CLASSES is JSON only; an upload view opts in with
    # MultiPartParser (docs/files.md) and must advertise only multipart.
    for operations in schema["paths"].values():
        for operation in operations.values():
            if not isinstance(operation, dict) or "requestBody" not in operation:
                continue
            content_types = set(operation["requestBody"]["content"])
            if "multipart/form-data" in content_types:
                assert content_types == {"multipart/form-data"}
            else:
                assert content_types == {"application/json"}


def test_list_documents_404_for_invalid_pages(schema: dict) -> None:
    listing = schema["paths"]["/api/v1/users/"]["get"]

    assert _response_ref(listing, "404") == ERROR_REF
    assert "415" not in listing["responses"]


def test_path_parameter_is_id_with_uuid_format(schema: dict) -> None:
    parameters = schema["paths"]["/api/v1/users/{id}/"]["get"]["parameters"]
    lookup = next(parameter for parameter in parameters if parameter["name"] == "id")

    assert lookup["in"] == "path"
    assert lookup["schema"]["type"] == "string"
    # The public contract is the path parameter name `{id}`, not `{public_id}`.
    assert "{public_id}" not in schema["paths"]


def test_request_components_forbid_additional_properties(schema: dict) -> None:
    assert (
        schema["components"]["schemas"]["PatchedUserRequest"]["additionalProperties"]
        is False
    )


def test_cookie_auth_documents_both_cookie_names(schema: dict) -> None:
    cookie = schema["components"]["securitySchemes"]["cookieAuth"]

    assert cookie["name"] == "sessionid"
    assert "__Secure-sessionid" in cookie["description"]
    assert "sessionid" in cookie["description"]


def test_pagination_examples_use_this_api(schema: dict) -> None:
    listing = schema["components"]["schemas"]["PaginatedUserList"]["properties"]

    assert "api.example.org/accounts/" not in listing["next"]["example"]
    assert "/api/v1/users/" in listing["next"]["example"]


def test_user_name_has_no_inherited_verbose_name(schema: dict) -> None:
    name = schema["components"]["schemas"]["User"]["properties"]["name"]
    # spectacular omits the title when it matches the field name.
    assert name.get("title", "Name") == "Name"
    assert "Name of User" not in str(schema)
