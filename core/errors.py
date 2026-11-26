"""
The API's one JSON error body, and Django's error views that answer with it.

`{"code", "detail"}`, plus `extra` for some domain errors and `fields` for
validation errors. The API exception handlers (`core.api.exceptions`) and the
plain Django handlers below build it here, so a request answered outside an
API view gets the same body and codes. Domain code, so it never imports the
API layer (docs/ARCHITECTURE.md#import-contracts). See docs/api.md#error-body.
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

# The codes the template itself answers with (docs/api.md#error-codes). A
# domain error carries the code its ApplicationError names.
BAD_REQUEST_CODE = "bad_request"
PARSE_ERROR_CODE = "parse_error"
VALIDATION_ERROR_CODE = "validation_error"
NOT_AUTHENTICATED_CODE = "not_authenticated"
PERMISSION_DENIED_CODE = "permission_denied"
NOT_FOUND_CODE = "not_found"
METHOD_NOT_ALLOWED_CODE = "method_not_allowed"
NOT_ACCEPTABLE_CODE = "not_acceptable"
PROTECTED_CODE = "protected"
UNSUPPORTED_MEDIA_TYPE_CODE = "unsupported_media_type"
THROTTLED_CODE = "throttled"
SERVER_ERROR_CODE = "server_error"

VALIDATION_ERROR_DETAIL = "Invalid input."
PROTECTED_DETAIL = "This resource has dependent records and cannot be deleted."
# The titles of Django's default error pages (django/views/defaults.py); Ninja's
# default 404 handler also says "Not Found" (ninja/errors.py). See ADR 0017.
BAD_REQUEST_DETAIL = "Bad Request"
FORBIDDEN_DETAIL = "Forbidden"
NOT_FOUND_DETAIL = "Not Found"
SERVER_ERROR_DETAIL = "Server Error"


# The API's JSON encoding (ADR 0021): no spaces, UTF-8 without \\u escapes, and
# never NaN or Infinity. The API's renderer (core.api.api.CompactJSONRenderer)
# and the error responses built outside Ninja both use it.
COMPACT_JSON: dict[str, object] = {
    "separators": (",", ":"),
    "ensure_ascii": False,
    "allow_nan": False,
}


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


def json_error(code: str, detail: str, status: int) -> JsonResponse:
    """An error body as the API renders it, for a response built outside Ninja."""
    return JsonResponse(
        error_body(code, detail), status=status, json_dumps_params=COMPACT_JSON
    )


def bad_request(request: HttpRequest, exception: Exception) -> JsonResponse:
    return json_error(BAD_REQUEST_CODE, BAD_REQUEST_DETAIL, HTTPStatus.BAD_REQUEST)


def permission_denied(request: HttpRequest, exception: Exception) -> JsonResponse:
    return json_error(PERMISSION_DENIED_CODE, FORBIDDEN_DETAIL, HTTPStatus.FORBIDDEN)


def page_not_found(request: HttpRequest, exception: Exception) -> JsonResponse:
    return json_error(NOT_FOUND_CODE, NOT_FOUND_DETAIL, HTTPStatus.NOT_FOUND)


def server_error(request: HttpRequest) -> JsonResponse:
    # Never echo the exception: it can contain internal details.
    return json_error(
        SERVER_ERROR_CODE, SERVER_ERROR_DETAIL, HTTPStatus.INTERNAL_SERVER_ERROR
    )
