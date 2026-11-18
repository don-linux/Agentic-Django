"""
The JSON error body is built without any API framework (core/errors.py).

The HTTP contract itself is tested in core/tests/http/test_error_format.py and
core/tests/http/test_error_views.py. See docs/api.md#error-body.
"""

from __future__ import annotations

import json
from http import HTTPStatus

from django.test import RequestFactory

from core.errors import PROTECTED_CODE
from core.errors import VALIDATION_ERROR_CODE
from core.errors import application_error_body
from core.errors import error_body
from core.errors import page_not_found
from core.errors import protected_error_body
from core.errors import validation_error_body
from core.exceptions import ApplicationError
from core.exceptions import ConflictError


def test_an_error_body_is_code_and_detail() -> None:
    assert error_body("not_found", "Not found.") == {
        "code": "not_found",
        "detail": "Not found.",
    }


def test_extra_is_added_only_when_given() -> None:
    assert error_body("x", "X.", extra={"available": 2}) == {
        "code": "x",
        "detail": "X.",
        "extra": {"available": 2},
    }


def test_an_application_error_keeps_its_code_message_and_extra() -> None:
    error = ConflictError("Stock is not enough.", code="insufficient_stock")
    with_extra = ApplicationError("No.", code="no", extra={"why": "rule"})

    assert application_error_body(error) == {
        "code": "insufficient_stock",
        "detail": "Stock is not enough.",
    }
    assert application_error_body(with_extra)["extra"] == {"why": "rule"}


def test_protected_and_validation_bodies_use_their_stable_codes() -> None:
    fields: dict[str, object] = {
        "name": [{"code": "max_length", "detail": "Too long."}]
    }

    assert protected_error_body()["code"] == PROTECTED_CODE == "protected"
    assert validation_error_body(fields) == {
        "code": VALIDATION_ERROR_CODE,
        "detail": "Invalid input.",
        "fields": fields,
    }


def test_the_plain_django_handlers_answer_the_same_body() -> None:
    response = page_not_found(RequestFactory().get("/nowhere/"), Exception())

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response["Content-Type"] == "application/json"
    assert json.loads(response.content) == {"code": "not_found", "detail": "Not found."}
