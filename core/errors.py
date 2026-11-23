"""
The API's one JSON error body, built without any API framework.

`{"code", "detail"}`, plus `extra` for some domain errors and `fields` for
validation errors. The API exception handlers (`core.api.exceptions`) and the
plain Django handlers below build it here, so another API framework reuses the
same body and codes. See docs/api.md#error-body.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import NotRequired
from typing import TypedDict

from django.http import JsonResponse

if TYPE_CHECKING:
    from django.http import HttpRequest

    from core.exceptions import ApplicationError

VALIDATION_ERROR_CODE = "validation_error"
VALIDATION_ERROR_DETAIL = "Invalid input."
PROTECTED_CODE = "protected"
PROTECTED_DETAIL = "This resource has dependent records and cannot be deleted."
# The titles of Django's default error pages (django/views/defaults.py); Ninja's
# default 404 handler also says "Not Found" (ninja/errors.py). See ADR 0017.
BAD_REQUEST_DETAIL = "Bad Request"
FORBIDDEN_DETAIL = "Forbidden"
NOT_FOUND_DETAIL = "Not Found"
SERVER_ERROR_DETAIL = "Server Error"


class ErrorItem(TypedDict):
    code: str
    detail: str


class ErrorBody(TypedDict):
    code: str
    detail: str
    extra: NotRequired[dict[str, object]]


class ValidationErrorBody(TypedDict):
    code: str
    detail: str
    fields: dict[str, object]


def error_body(
    code: str, detail: str, *, extra: dict[str, object] | None = None
) -> ErrorBody:
    body: ErrorBody = {"code": code, "detail": detail}
    if extra is not None:
        body["extra"] = extra
    return body


def application_error_body(exc: ApplicationError) -> ErrorBody:
    return error_body(exc.code, exc.message, extra=exc.extra)


def protected_error_body() -> ErrorBody:
    return error_body(PROTECTED_CODE, PROTECTED_DETAIL)


def validation_error_body(fields: dict[str, object]) -> ValidationErrorBody:
    return {
        "code": VALIDATION_ERROR_CODE,
        "detail": VALIDATION_ERROR_DETAIL,
        "fields": fields,
    }


# handler400/403/404/500 (config/urls.py) for requests that never reach an API
# view. See docs/api.md#errors-outside-api-views.


def _error(code: str, detail: str, status: HTTPStatus) -> JsonResponse:
    return JsonResponse(error_body(code, detail), status=status)


def bad_request(request: HttpRequest, exception: Exception) -> JsonResponse:
    return _error("bad_request", BAD_REQUEST_DETAIL, HTTPStatus.BAD_REQUEST)


def permission_denied(request: HttpRequest, exception: Exception) -> JsonResponse:
    return _error("permission_denied", FORBIDDEN_DETAIL, HTTPStatus.FORBIDDEN)


def page_not_found(request: HttpRequest, exception: Exception) -> JsonResponse:
    return _error("not_found", NOT_FOUND_DETAIL, HTTPStatus.NOT_FOUND)


def server_error(request: HttpRequest) -> JsonResponse:
    # Never echo the exception: it can contain internal details.
    return _error("server_error", SERVER_ERROR_DETAIL, HTTPStatus.INTERNAL_SERVER_ERROR)
