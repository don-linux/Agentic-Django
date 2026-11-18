"""
Global DRF exception handler: every error gets the `{"code", "detail"}` body.

The body and its codes come from core.errors, which no API framework owns. See
docs/api.md#error-body; new codes go in its error-code table.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Any

from django.core.exceptions import NON_FIELD_ERRORS
from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import ProtectedError
from django.db.models import RestrictedError
from django.http import Http404
from rest_framework import exceptions as drf_exceptions
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import exception_handler
from rest_framework.views import set_rollback

from core.errors import application_error_body
from core.errors import error_body
from core.errors import protected_error_body
from core.errors import validation_error_body
from core.exceptions import ApplicationError

if TYPE_CHECKING:
    from rest_framework.exceptions import ErrorDetail

    from core.errors import ErrorBody
    from core.errors import ErrorItem
    from core.errors import ValidationErrorBody


def api_exception_handler(
    exc: Exception,
    context: dict[str, Any],
) -> Response | None:
    if isinstance(exc, ApplicationError):
        set_rollback()
        return Response(application_error_body(exc), status=exc.status_code)

    if isinstance(exc, (ProtectedError, RestrictedError)):
        set_rollback()
        return Response(protected_error_body(), status=HTTPStatus.CONFLICT)

    if isinstance(exc, DjangoValidationError):
        exc = drf_exceptions.ValidationError(_django_validation_detail(exc))
    elif isinstance(exc, ObjectDoesNotExist):
        exc = Http404()

    response = exception_handler(exc, context)
    if response is None:
        return None

    # Replace only the body: DRF already set headers such as WWW-Authenticate
    # and Retry-After on this Response.
    if isinstance(exc, drf_exceptions.ValidationError):
        response.data = _validation_error_body(response.data)
    else:
        response.data = _api_error_body(exc, response.data)
    return response


def _django_validation_detail(exc: DjangoValidationError) -> Any:
    detail = serializers.as_serializer_error(exc)
    if NON_FIELD_ERRORS in detail:
        detail[api_settings.NON_FIELD_ERRORS_KEY] = [
            *detail.get(api_settings.NON_FIELD_ERRORS_KEY, []),
            *detail.pop(NON_FIELD_ERRORS),
        ]
    return detail


def _api_error_body(exc: Exception, data: Any) -> ErrorBody:
    if isinstance(data, dict) and set(data) == {"detail"}:
        item = _error_item(data["detail"])
        return error_body(item["code"], item["detail"])
    # An APIException raised with a list or dict detail.
    default_code = getattr(exc, "default_code", "error")
    default_detail = getattr(exc, "default_detail", "")
    return error_body(str(default_code), str(default_detail))


def _validation_error_body(data: Any) -> ValidationErrorBody:
    if isinstance(data, dict):
        fields = _format_errors(data)
    else:
        fields = {api_settings.NON_FIELD_ERRORS_KEY: _format_errors(data)}
    return validation_error_body(fields)


def _format_errors(detail: Any) -> Any:
    """Turn DRF error details into {"code", "detail"} items, keeping their nesting."""
    if isinstance(detail, dict):
        return {str(key): _format_errors(value) for key, value in detail.items()}
    if isinstance(detail, list):
        return [_format_errors(item) for item in detail]
    return _error_item(detail)


def _error_item(detail: ErrorDetail | str) -> ErrorItem:
    return {"code": getattr(detail, "code", None) or "invalid", "detail": str(detail)}
