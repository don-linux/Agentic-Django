"""docs/openapi.json declares every status the runtime answers (docs/api.md).

Every operation lists 401/403/406/429, writes add 400/415, lookups add 404,
paginated lists add 404 and 400, and the WWW-Authenticate and Retry-After
headers are documented. oasdiff does not flag a removed error response, so
this test is the only guard.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Any

import pytest
from django.test import Client

from core.api.api import api

if TYPE_CHECKING:
    from apps.users.models import User

COMMON = {"401", "403", "406", "429"}
WRITE = {"400", "415"}


def _operations() -> dict[tuple[str, str], dict[str, Any]]:
    paths = api.get_openapi_schema()["paths"]
    return {(p, m.upper()): op for p, item in paths.items() for m, op in item.items()}


def test_every_operation_declares_the_shared_error_responses() -> None:
    for (path, method), op in _operations().items():
        codes = {str(code) for code in op["responses"]}
        # The success status depends on the operation: 200, 201 (create), 202, 204.
        assert any(code.startswith("2") for code in codes), (path, method)
        expected = set(COMMON)
        if method in {"POST", "PUT", "PATCH"}:
            expected |= WRITE
        if "{" in path or op["operationId"].endswith("_list"):
            expected.add("404")
        if op["operationId"].endswith("_list"):
            # A malformed page or page_size (docs/api.md#pagination).
            expected.add("400")
        assert expected <= codes, (path, method, sorted(expected - codes))


def test_error_responses_document_their_headers() -> None:
    for op in _operations().values():
        responses = {str(code): body for code, body in op["responses"].items()}
        assert "WWW-Authenticate" in responses["401"]["headers"]
        assert responses["429"]["headers"]["Retry-After"]["schema"] == {
            "type": "integer"
        }
        assert responses["406"]["description"] != "Not Acceptable"


def test_read_only_and_non_nullable_fields_match_the_runtime() -> None:
    schemas = api.get_openapi_schema()["components"]["schemas"]
    user = schemas["UserOut"]["properties"]
    assert user["id"]["readOnly"] is True
    assert user["url"]["readOnly"] is True
    # null is a 400 string_type at runtime, so the request schema must not offer it.
    assert schemas["UserUpdateIn"]["properties"]["name"] == {
        "maxLength": 255,
        "title": "Name",
        "type": "string",
    }


@pytest.mark.django_db
def test_statuses_seen_at_runtime_are_declared(user: User) -> None:
    ops = _operations()
    other = "00000000-0000-4000-8000-000000000000"
    client = Client()
    client.force_login(user)
    probes = [
        ("GET", "/api/v1/users/", {}, None),
        ("GET", "/api/v1/users/?page=99", {}, None),
        ("GET", "/api/v1/users/?page=abc", {}, None),
        ("GET", "/api/v1/users/?page_size=0", {}, None),
        ("GET", "/api/v1/users/me/", {"HTTP_ACCEPT": "text/html"}, None),
        ("PATCH", "/api/v1/users/me/", {}, "name=x"),
        ("PATCH", "/api/v1/users/me/", {}, '{"name": null}'),
        ("GET", f"/api/v1/users/{other}/", {}, None),
        ("PATCH", f"/api/v1/users/{user.public_id}/", {}, '{"name": "x"}'),
    ]
    for method, url, extra, body in probes:
        kwargs: dict[str, Any] = dict(extra)
        if body is not None:
            ctype = "application/json" if body.startswith("{") else "text/plain"
            kwargs.update(data=body, content_type=ctype)
        response = client.generic(method, url, **kwargs)
        template = url.split("?")[0].replace(str(user.public_id), "{id}")
        template = template.replace(other, "{id}")
        op = ops[(template, method)]
        declared = {str(code) for code in op["responses"]}
        assert str(response.status_code) in declared, (
            method,
            url,
            response.status_code,
        )
    anonymous = Client().get("/api/v1/users/me/")
    assert anonymous.status_code == HTTPStatus.UNAUTHORIZED
