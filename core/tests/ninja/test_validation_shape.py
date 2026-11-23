"""The 400 `fields` tree does not depend on Ninja's parameter names (ADR 0006).

Ninja's error loc is (source, parameter name, field...) for a body and
(source, name) for query/path. Only the first one or two parts are framework
plumbing; a schema field may itself be called body, path or query. Each item
keeps Pydantic's own error type and message (docs/api.md#error-body).
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from ninja import Router
from ninja.testing import TestClient

from core.api.api import ContractAPI
from core.api.exceptions import _ninja_validation_fields
from core.api.exceptions import register_exception_handlers
from core.api.parsers import JSONParser
from core.api.schemas import InputSchema
from core.tests.ninja.pagination_views import cursor_api

if TYPE_CHECKING:
    from django.test import Client


class Address(InputSchema):
    city: str


class Payload(InputSchema):
    title: str
    body: str
    path: str
    page: int
    address: Address
    tags: list[int]


def _client() -> TestClient:
    api = ContractAPI(
        urls_namespace=f"validation-shape-{id(object())}",
        auth=None,
        parser=JSONParser(),
    )
    register_exception_handlers(api)
    router = Router()

    @router.post("/named-payload/", auth=None)
    def named_payload(request, payload: Payload):
        return {"ok": True}

    @router.post("/named-body/", auth=None)
    def named_body(request, body: Payload):
        return {"ok": True}

    @router.get("/query-page/", auth=None)
    def query_page(request, page: int, page_size: int = 1, other: int = 1):
        return {"ok": True}

    api.add_router("/", router)
    return TestClient(api)


def _post(path: str, data: object):
    # ninja.testing builds a Mock request: content_type must be set explicitly.
    return _client().post(path, json=data, content_type="application/json")


BAD = {"body": 1, "path": 2, "page": "x", "address": {}, "tags": [1, "y"]}
# Pydantic's error types and messages:
# https://docs.pydantic.dev/latest/errors/validation_errors/
MISSING = [{"code": "missing", "detail": "Field required"}]
NOT_A_STRING = [{"code": "string_type", "detail": "Input should be a valid string"}]
NOT_AN_INTEGER = [
    {
        "code": "int_parsing",
        "detail": (
            "Input should be a valid integer, unable to parse string as an integer"
        ),
    }
]


def _fields(response) -> dict:
    assert response.status_code == HTTPStatus.BAD_REQUEST, response.content
    body = response.json()
    assert body["code"] == "validation_error"
    return body["fields"]


def test_field_errors_are_keyed_by_field_whatever_the_parameter_name() -> None:
    expected = {
        "title": MISSING,
        "body": NOT_A_STRING,
        "path": NOT_A_STRING,
        "page": NOT_AN_INTEGER,
        "address": {"city": MISSING},
        "tags": {"1": NOT_AN_INTEGER},
    }
    assert _fields(_post("/named-payload/", BAD)) == expected
    assert _fields(_post("/named-body/", BAD)) == expected


def test_a_body_field_named_page_is_a_400_not_an_invalid_page_404() -> None:
    response = _post(
        "/named-body/",
        {
            "title": "t",
            "body": "b",
            "path": "p",
            "page": "x",
            "address": {"city": "c"},
            "tags": [],
        },
    )
    assert _fields(response) == {"page": NOT_AN_INTEGER}


@pytest.mark.parametrize("query", ["?page=x", "?page=1&page_size=x"])
def test_an_invalid_page_or_page_size_query_parameter_is_the_invalid_page_404(
    query: str,
) -> None:
    response = _client().get(f"/query-page/{query}")
    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json() == {"code": "not_found", "detail": "Not Found"}


def test_another_invalid_query_parameter_is_a_400_field_error() -> None:
    response = _client().get("/query-page/?page=1&other=x")
    assert _fields(response) == {"other": NOT_AN_INTEGER}


def test_an_error_loc_without_a_parameter_source_is_kept_whole() -> None:
    fields = _ninja_validation_fields(
        [
            {"loc": ("custom", "x"), "type": "missing", "msg": "m"},
            {"loc": (), "type": "missing", "msg": "m"},
        ]
    )
    missing = [{"code": "missing", "detail": "m"}]
    assert fields == {"custom": {"x": missing}, "non_field_errors": missing}


def test_an_unknown_field_is_unknown_field_whatever_its_value() -> None:
    # Pydantic's extra_forbidden is the registered code unknown_field (ADR 0006),
    # with Pydantic's message, at any depth and for any value, None included.
    valid = {"title": "t", "body": "b", "path": "p", "page": 1, "tags": []}
    response = _post(
        "/named-body/", {**valid, "address": {"city": "c", "zip": None}, "x": None}
    )
    unknown = [{"code": "unknown_field", "detail": "Extra inputs are not permitted"}]
    assert _fields(response) == {"address": {"zip": unknown}, "x": unknown}


def test_a_body_that_is_not_an_object_is_a_non_field_error() -> None:
    assert _fields(_post("/named-body/", [1, 2])) == {
        "non_field_errors": [
            {
                "code": "model_attributes_type",
                "detail": (
                    "Input should be a valid dictionary or object to extract "
                    "fields from"
                ),
            }
        ]
    }


@pytest.mark.django_db
def test_malformed_json_is_ninja_s_parse_error(user_client: Client) -> None:
    response = user_client.patch(
        "/api/v1/users/me/", data='{"name": ', content_type="application/json"
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["code"] == "parse_error"
    # ninja/params/models.py: any parser failure is "Cannot parse request body".
    assert response.json()["detail"] == "Cannot parse request body"


@pytest.mark.django_db
def test_an_invalid_cursor_is_a_cursor_field_error() -> None:
    response = TestClient(cursor_api()).get("/?cursor=not-a-cursor")
    assert _fields(response) == {
        "cursor": [{"code": "invalid", "detail": "Invalid Cursor"}]
    }


def test_null_for_a_non_nullable_field_is_pydantic_s_string_type() -> None:
    fields = _fields(_post("/named-body/", {**BAD, "title": None}))
    assert fields["title"] == NOT_A_STRING
