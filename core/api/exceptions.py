"""
Ninja exception handlers that delegate to core.errors (ADR 0006).

Ninja defaults: 422 and {"detail"}. These handlers emit {code, detail}
(plus fields/extra) with the existing status codes.
Sources: Context7 /websites/django-ninja_dev "Override Default Validation
Error Handler"; django-ninja 1.8.0 ninja/errors.py.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Any

from django.core.exceptions import NON_FIELD_ERRORS
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import ProtectedError
from django.db.models import RestrictedError
from django.http import Http404
from django.http import HttpRequest
from django.http import HttpResponse
from ninja.errors import AuthenticationError
from ninja.errors import AuthorizationError
from ninja.errors import HttpError
from ninja.errors import Throttled
from ninja.errors import ValidationError as NinjaValidationError

from core.api.pagination import INVALID_PAGE
from core.errors import application_error_body
from core.errors import error_body
from core.errors import protected_error_body
from core.errors import validation_error_body
from core.exceptions import ApplicationError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ninja import NinjaAPI


def _field_item(message: str, code: str = "invalid") -> dict[str, str]:
    return {"code": code, "detail": message}


def _ninja_validation_fields(errors: list[dict[str, Any]]) -> dict[str, Any]:
    """Turn Ninja/Pydantic error dicts into the nested {code, detail} tree."""
    fields: dict[str, Any] = {}
    for err in errors:
        loc = [
            part
            for part in err.get("loc", ())
            if part not in {"body", "query", "path", "header", "form"}
        ]
        if not loc:
            loc = ["non_field_errors"]
        typ = str(err.get("type") or "invalid")
        msg = str(err.get("msg") or "Invalid input.")
        # extra="forbid" → unknown_field, same wording as the previous serializer.
        if typ in {"extra_forbidden", "unexpected_keyword_argument"}:
            code = "unknown_field"
            msg = "Unknown field."
        elif (
            typ in {"none_required", "none_type", "null"}
            or (typ == "string_type" and "input" in err and err.get("input") is None)
            or (typ == "value_error" and "null" in msg.lower())
        ):
            code = "null"
        elif typ in {"string_too_long", "too_long"}:
            code = "max_length"
        elif typ in {"string_too_short", "too_short"}:
            code = "min_length"
        else:
            code = "invalid"
        cursor: dict[str, Any] = fields
        for part in loc[:-1]:
            key = str(part)
            nxt = cursor.setdefault(key, {})
            if not isinstance(nxt, dict):
                nxt = {}
                cursor[key] = nxt
            cursor = nxt
        leaf = str(loc[-1])
        cursor.setdefault(leaf, []).append(_field_item(msg, code))
    return fields


def _django_validation_fields(exc: DjangoValidationError) -> dict[str, Any]:
    if hasattr(exc, "error_dict"):
        fields: dict[str, Any] = {}
        for field, errors in exc.error_dict.items():
            key = "non_field_errors" if field == NON_FIELD_ERRORS else field
            items = []
            for err in errors:
                message = err.messages[0] if err.messages else str(err)
                items.append(_field_item(message, err.code or "invalid"))
            fields[key] = items
        return fields
    return {
        "non_field_errors": [
            _field_item(msg, getattr(exc, "code", None) or "invalid")
            for msg in exc.messages
        ]
    }


def _http_error_code(status: int, message: str) -> str:
    """The {code} for an HttpError that is not a 401 (docs/api.md#error-codes)."""
    if status == HTTPStatus.BAD_REQUEST and "parse" in message.lower():
        return "parse_error"
    if status == HTTPStatus.UNSUPPORTED_MEDIA_TYPE:
        return "unsupported_media_type"
    if status == HTTPStatus.NOT_ACCEPTABLE:
        return "not_acceptable"
    if status == HTTPStatus.FORBIDDEN and "csrf" in message.lower():
        return "permission_denied"
    return "error"


class _ErrorHandlers:
    """The API's exception handlers, bound to the NinjaAPI that renders them."""

    def __init__(self, api: NinjaAPI) -> None:
        self.api = api

    def _json(
        self, request: HttpRequest, body: Mapping[str, Any], status: int
    ) -> HttpResponse:
        return self.api.create_response(request, body, status=status)

    def on_application_error(
        self, request: HttpRequest, exc: ApplicationError
    ) -> HttpResponse:
        return self._json(request, application_error_body(exc), exc.status_code)

    def on_protected(self, request: HttpRequest, exc: Exception) -> HttpResponse:
        return self._json(request, protected_error_body(), HTTPStatus.CONFLICT)

    def on_django_validation(
        self, request: HttpRequest, exc: DjangoValidationError
    ) -> HttpResponse:
        return self._json(
            request,
            validation_error_body(_django_validation_fields(exc)),
            HTTPStatus.BAD_REQUEST,
        )

    def on_ninja_validation(
        self, request: HttpRequest, exc: NinjaValidationError
    ) -> HttpResponse:
        for err in exc.errors:
            loc = [str(part) for part in err.get("loc", ())]
            if "page" in loc or "page_size" in loc:
                return self.on_404(request, Http404(INVALID_PAGE))
        return self._json(
            request,
            validation_error_body(_ninja_validation_fields(exc.errors)),
            HTTPStatus.BAD_REQUEST,
        )

    def on_404(self, request: HttpRequest, exc: Http404) -> HttpResponse:
        detail = str(exc) or "Not found."
        # Django wraps the message as "Http404('Invalid page.')" in some paths;
        # prefer the explicit argument when present.
        args = getattr(exc, "args", ())
        if args and isinstance(args[0], str) and args[0]:
            detail = args[0]
        return self._json(
            request, error_body("not_found", detail), HTTPStatus.NOT_FOUND
        )

    def on_auth(self, request: HttpRequest, exc: AuthenticationError) -> HttpResponse:
        return self._not_authenticated(request)

    def _not_authenticated(self, request: HttpRequest) -> HttpResponse:
        response = self._json(
            request,
            error_body(
                "not_authenticated", "Authentication credentials were not provided."
            ),
            HTTPStatus.UNAUTHORIZED,
        )
        response["WWW-Authenticate"] = "X-Session-Token"
        return response

    def on_forbidden(
        self, request: HttpRequest, exc: AuthorizationError
    ) -> HttpResponse:
        return self._json(
            request,
            error_body(
                "permission_denied",
                "You do not have permission to perform this action.",
            ),
            HTTPStatus.FORBIDDEN,
        )

    def on_django_permission_denied(
        self, request: HttpRequest, exc: PermissionDenied
    ) -> HttpResponse:
        return self.on_forbidden(request, AuthorizationError())

    def on_throttled(self, request: HttpRequest, exc: Throttled) -> HttpResponse:
        # When a throttle refuses the request, Operation._check_throttles calls
        # this handler and then sets Retry-After on its response (ninja/operation.py
        # in 1.8.0). Set it here too, for a Throttled raised directly.
        response = self._json(
            request,
            error_body("throttled", "Request was throttled."),
            HTTPStatus.TOO_MANY_REQUESTS,
        )
        wait = getattr(exc, "wait", None)
        if wait is not None:
            response["Retry-After"] = str(int(wait))
        return response

    def on_http_error(self, request: HttpRequest, exc: HttpError) -> HttpResponse:
        message = str(exc)
        status = exc.status_code
        if status == HTTPStatus.UNAUTHORIZED:
            return self._not_authenticated(request)
        code = _http_error_code(status, message)
        return self._json(request, error_body(code, message), status)


def register_exception_handlers(api: NinjaAPI) -> None:
    """
    Register the {code, detail} handlers on api.

    @api.exception_handler(exc_class) is called on the bound methods: a handler
    is looked up along the exception's MRO, so the order does not matter
    (https://django-ninja.dev/guide/errors/, "Custom exception handlers").
    add_exception_handler, which the decorator wraps, types the handler's
    second argument as the exception or its class (ninja.main.ExcHandler in
    1.8.0), and a handler only ever receives the instance.
    """
    handlers = _ErrorHandlers(api)
    api.exception_handler(ApplicationError)(handlers.on_application_error)
    api.exception_handler(ProtectedError)(handlers.on_protected)
    api.exception_handler(RestrictedError)(handlers.on_protected)
    api.exception_handler(DjangoValidationError)(handlers.on_django_validation)
    api.exception_handler(NinjaValidationError)(handlers.on_ninja_validation)
    api.exception_handler(Http404)(handlers.on_404)
    api.exception_handler(AuthenticationError)(handlers.on_auth)
    api.exception_handler(AuthorizationError)(handlers.on_forbidden)
    api.exception_handler(PermissionDenied)(handlers.on_django_permission_denied)
    api.exception_handler(Throttled)(handlers.on_throttled)
    api.exception_handler(HttpError)(handlers.on_http_error)
