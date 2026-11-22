"""
Ninja exception handlers that delegate to core.errors (ADR 0006).

Ninja defaults: 422 and {"detail"}. These handlers emit {code, detail}
(plus fields/extra) with the existing status codes.
Sources: Context7 /websites/django-ninja_dev "Override Default Validation
Error Handler"; django-ninja 1.8.0 ninja/errors.py.
"""

from __future__ import annotations

import math
from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Any

from django.core.exceptions import NON_FIELD_ERRORS
from django.core.exceptions import ObjectDoesNotExist
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
    from collections.abc import Sequence

    from ninja import NinjaAPI


def _field_item(message: str, code: str = "invalid") -> dict[str, str]:
    return {"code": code, "detail": message}


# Ninja's loc starts with the parameter source; a body loc also carries the
# body parameter's name (ninja/params/models.py BodyModel, __read_from_single_attr__).
_SOURCES = frozenset({"query", "path", "header", "cookie", "form", "file"})

# Pydantic error type -> (code, detail) in DRF's wording. Clients branch on
# `code` (docs/api.md#error-body); `detail` stays readable and close to the old text.
_MESSAGES: dict[str, tuple[str, str]] = {
    "missing": ("required", "This field is required."),
    "string_type": ("invalid", "Not a valid string."),
    "int_type": ("invalid", "A valid integer is required."),
    "int_parsing": ("invalid", "A valid integer is required."),
    "int_from_float": ("invalid", "A valid integer is required."),
    "bool_type": ("invalid", "Must be a valid boolean."),
    "bool_parsing": ("invalid", "Must be a valid boolean."),
    "uuid_type": ("invalid", "Must be a valid UUID."),
    "uuid_parsing": ("invalid", "Must be a valid UUID."),
    "decimal_type": ("invalid", "A valid number is required."),
    "decimal_parsing": ("invalid", "A valid number is required."),
    "float_type": ("invalid", "A valid number is required."),
    "float_parsing": ("invalid", "A valid number is required."),
    "list_type": ("not_a_list", "Expected a list of items."),
}
_NOT_AN_OBJECT = frozenset({"model_type", "model_attributes_type", "dict_type"})
_UNKNOWN_FIELD = frozenset({"extra_forbidden", "unexpected_keyword_argument"})


def _error_path(loc: Sequence[object]) -> list[str]:
    parts = [str(part) for part in loc]
    if parts[:1] == ["body"]:
        return parts[2:]
    if parts[:1] and parts[0] in _SOURCES:
        return parts[1:]
    return parts


_LENGTH = frozenset({"string_too_long", "too_long", "string_too_short", "too_short"})


def _length(typ: str, msg: str, ctx: Mapping[str, Any]) -> tuple[str, str]:
    if typ == "string_too_long" and ctx.get("max_length") is not None:
        msg = f"Ensure this field has no more than {ctx['max_length']} characters."
    elif typ == "string_too_short" and ctx.get("min_length") is not None:
        msg = f"Ensure this field has at least {ctx['min_length']} characters."
    return ("max_length" if "long" in typ else "min_length"), msg


def _code_and_detail(err: Mapping[str, Any]) -> tuple[str, str]:
    typ = str(err.get("type") or "invalid")
    msg = str(err.get("msg") or "Invalid input.")
    # extra="forbid" -> unknown_field, same wording as the previous serializer.
    if typ in _UNKNOWN_FIELD:
        return "unknown_field", "Unknown field."
    # input_type comes from ContractAPI.validation_error_from_error_contexts
    # (Ninja drops the raw input): a None sent to a non-nullable field is "null".
    input_type = err.get("input_type")
    if typ == "value_error" and "null" in msg.lower():
        return "null", msg
    if input_type == "NoneType" and typ != "missing":
        return "null", "This field may not be null."
    return _code_and_detail_by_type(typ, msg, err.get("ctx") or {}, input_type)


def _code_and_detail_by_type(
    typ: str, msg: str, ctx: Mapping[str, Any], input_type: object
) -> tuple[str, str]:
    if typ == "null_characters_not_allowed":
        return typ, msg
    if typ in _LENGTH:
        return _length(typ, msg, ctx)
    if typ in _NOT_AN_OBJECT:
        got = input_type or "another type"
        return "invalid", f"Invalid data. Expected a dictionary, but got {got}."
    return _MESSAGES.get(typ, ("invalid", msg))


def _ninja_validation_fields(errors: list[dict[str, Any]]) -> dict[str, Any]:
    """Turn Ninja/Pydantic error dicts into the nested {code, detail} tree."""
    fields: dict[str, Any] = {}
    for err in errors:
        if "loc" not in err and len(err) == 1:
            # ninja.pagination: ValidationError([{"cursor": "Invalid Cursor"}]).
            ((name, _message),) = err.items()
            fields.setdefault(str(name), []).append(
                _field_item(f"Invalid {name}.", "invalid")
            )
            continue
        loc = _error_path(err.get("loc", ())) or ["non_field_errors"]
        code, msg = _code_and_detail(err)
        cursor: dict[str, Any] = fields
        for key in loc[:-1]:
            nxt = cursor.setdefault(key, {})
            if not isinstance(nxt, dict):
                nxt = {}
                cursor[key] = nxt
            cursor = nxt
        cursor.setdefault(loc[-1], []).append(_field_item(msg, code))
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
    # One item per error, each with its own code (DRF's as_serializer_error).
    return {
        "non_field_errors": [
            _field_item(
                err.messages[0] if err.messages else str(err), err.code or "invalid"
            )
            for err in exc.error_list
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
            # Only the page/page_size query parameters; a body field may be "page".
            if loc[:1] == ["query"] and ("page" in loc or "page_size" in loc):
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

    def on_does_not_exist(
        self, request: HttpRequest, exc: ObjectDoesNotExist
    ) -> HttpResponse:
        # Model.DoesNotExist from a selector or service: 404, never 500 (ADR 0006).
        return self.on_404(request, Http404())

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
        detail = str(exc.args[0]) if exc.args and exc.args[0] else None
        if detail is None:
            return self.on_forbidden(request, AuthorizationError())
        return self._json(
            request, error_body("permission_denied", detail), HTTPStatus.FORBIDDEN
        )

    def on_throttled(self, request: HttpRequest, exc: Throttled) -> HttpResponse:
        # When a throttle refuses the request, Operation._check_throttles calls
        # this handler and then sets Retry-After on its response (ninja/operation.py
        # in 1.8.0). Set it here too, for a Throttled raised directly.
        wait = getattr(exc, "wait", None)
        # DRF's Throttled: whole seconds, rounded up, for the detail and the header.
        seconds = None if wait is None else math.ceil(wait)
        detail = "Request was throttled."
        if seconds is not None:
            unit = "second" if seconds == 1 else "seconds"
            detail = f"{detail} Expected available in {seconds} {unit}."
        response = self._json(
            request, error_body("throttled", detail), HTTPStatus.TOO_MANY_REQUESTS
        )
        if seconds is not None:
            # Same value Operation._check_throttles sets (ceil, RFC 9110).
            response["Retry-After"] = str(seconds)
        return response

    def on_http_error(self, request: HttpRequest, exc: HttpError) -> HttpResponse:
        message = str(exc)
        status = exc.status_code
        # BodyModel.get_request_data wraps any parser error as HttpError(400,
        # "Cannot parse request body") from the original (ninja/params/models.py);
        # keep core.api.parsers' "JSON parse error - ..." detail (docs/api.md).
        cause = exc.__cause__
        if isinstance(cause, HttpError):
            # The parser's own 400/415 (core.api.parsers), not Ninja's wrapper.
            message = str(cause)
            status = cause.status_code
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
    api.exception_handler(ObjectDoesNotExist)(handlers.on_does_not_exist)
    api.exception_handler(AuthenticationError)(handlers.on_auth)
    api.exception_handler(AuthorizationError)(handlers.on_forbidden)
    api.exception_handler(PermissionDenied)(handlers.on_django_permission_denied)
    api.exception_handler(Throttled)(handlers.on_throttled)
    api.exception_handler(HttpError)(handlers.on_http_error)
