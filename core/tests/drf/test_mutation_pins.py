"""Pins for behaviour mutation testing found unguarded in core's DRF code."""

from __future__ import annotations

import json
from http import HTTPStatus

from django.test import RequestFactory
from rest_framework import exceptions as drf_exceptions

from core.api.exceptions import _error_item
from core.api.exceptions import api_exception_handler
from core.cache_backend import DEFAULT_MAX_TTL  # noqa: F401
from core.errors import bad_request
from core.errors import page_not_found
from core.errors import permission_denied
from core.errors import server_error


def test_api_exception_with_list_detail_uses_default_code_and_detail() -> None:
    class NotAcceptable(drf_exceptions.APIException):
        status_code = HTTPStatus.NOT_ACCEPTABLE
        default_code = "not_acceptable"
        default_detail = "Not acceptable."

    response = api_exception_handler(NotAcceptable(detail=["a", "b"]), {"view": None})
    assert response is not None
    assert response.status_code == HTTPStatus.NOT_ACCEPTABLE
    assert response.data == {"code": "not_acceptable", "detail": "Not acceptable."}


def test_api_exception_with_dict_detail_uses_default_code_and_detail() -> None:
    class Conflict(drf_exceptions.APIException):
        status_code = HTTPStatus.CONFLICT
        default_code = "conflict"
        default_detail = "Conflict."

    response = api_exception_handler(Conflict(detail={"field": "x"}), {"view": None})
    assert response is not None
    assert response.status_code == HTTPStatus.CONFLICT
    assert response.data == {"code": "conflict", "detail": "Conflict."}


def test_json_error_handlers_lock_the_detail_text() -> None:
    request = RequestFactory().get("/")
    cases = [
        (
            bad_request(request, Exception()),
            HTTPStatus.BAD_REQUEST,
            "bad_request",
            "Bad request.",
        ),
        (
            permission_denied(request, Exception()),
            HTTPStatus.FORBIDDEN,
            "permission_denied",
            "You do not have permission to perform this action.",
        ),
        (
            page_not_found(request, Exception()),
            HTTPStatus.NOT_FOUND,
            "not_found",
            "Not found.",
        ),
        (
            server_error(request),
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "server_error",
            "A server error occurred.",
        ),
    ]
    for response, status, code, detail in cases:
        assert response.status_code == status
        body = json.loads(response.content)
        assert body == {"code": code, "detail": detail}


def test_error_item_defaults_plain_strings_to_invalid() -> None:
    assert _error_item("Nope.") == {"code": "invalid", "detail": "Nope."}
