"""
Ninja exception handlers that delegate to core.errors (ADR 0006).

Ninja defaults: 422 and {"detail"}. These handlers emit {code, detail}
(plus fields/extra) with the existing status codes. Each text comes from the
code that raised the error: Pydantic, Ninja or Django (ADR 0017).
Sources: Context7 /websites/django-ninja_dev "Override Default Validation
Error Handler"; django-ninja 1.8.0 ninja/errors.py.
"""

from __future__ import annotations

import math
from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Any
from typing import NoReturn

from django.core.exceptions import NON_FIELD_ERRORS
from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import connections
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

from core.errors import FORBIDDEN_DETAIL
from core.errors import NOT_FOUND_DETAIL
from core.errors import application_error_body
from core.errors import error_body
from core.errors import protected_error_body
from core.errors import validation_error_body
from core.exceptions import ApplicationError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from collections.abc import Sequence

    from ninja import NinjaAPI


def _set_rollback() -> None:
    # With ATOMIC_REQUESTS, a handled error must not commit the view's earlier
    # writes: the handler turns the exception into a response, so Django's
    # transaction would otherwise commit.
    for connection in connections.all(initialized_only=True):
        if connection.settings_dict["ATOMIC_REQUESTS"] and connection.in_atomic_block:
            connection.set_rollback(True)


def _field_item(message: str, code: str) -> dict[str, str]:
    return {"code": code, "detail": message}


# Ninja's loc starts with the parameter source; a body loc also carries the
# body parameter's name (ninja/params/models.py BodyModel, __read_from_single_attr__).
_SOURCES = frozenset({"query", "path", "header", "cookie", "form", "file"})

# A key that is not a field (extra="forbid") is the registered code unknown_field
# (ADR 0006, docs/api.md#error-codes). Every other field error keeps Pydantic's
# own type and message: https://docs.pydantic.dev/latest/errors/validation_errors/
_EXTRA_FORBIDDEN = "extra_forbidden"
_UNKNOWN_FIELD = "unknown_field"


def _error_path(loc: Sequence[object]) -> list[str]:
    parts = [str(part) for part in loc]
    if parts[:1] == ["body"]:
        return parts[2:]
    if parts[:1] and parts[0] in _SOURCES:
        return parts[1:]
    return parts


def _code_and_detail(err: Mapping[str, Any]) -> tuple[str, str]:
    typ = str(err["type"])
    code = _UNKNOWN_FIELD if typ == _EXTRA_FORBIDDEN else typ
    return code, str(err["msg"])


def _ninja_validation_fields(errors: list[dict[str, Any]]) -> dict[str, Any]:
    """Turn Ninja/Pydantic error dicts into the nested {code, detail} tree."""
    fields: dict[str, Any] = {}
    for err in errors:
        if "loc" not in err and len(err) == 1:
            # ninja.pagination: ValidationError([{"cursor": "Invalid Cursor"}]).
            # Ninja gives a message and no type: the generic code is invalid.
            ((name, message),) = err.items()
            fields.setdefault(str(name), []).append(
                _field_item(str(message), "invalid")
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
    # One item per error, each with its own code (invalid when Django gives none).
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
        _set_rollback()
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
        return self._json(
            request,
            validation_error_body(_ninja_validation_fields(exc.errors)),
            HTTPStatus.BAD_REQUEST,
        )

    def on_404(self, request: HttpRequest, exc: Http404) -> HttpResponse:
        # Ninja's default 404 text. Like Ninja outside DEBUG, the handler never
        # repeats the exception's message, which may name a model.
        return self._json(
            request, error_body("not_found", NOT_FOUND_DETAIL), HTTPStatus.NOT_FOUND
        )

    def on_does_not_exist(
        self, request: HttpRequest, exc: ObjectDoesNotExist
    ) -> HttpResponse:
        # Model.DoesNotExist from a selector or service: 404, never 500 (ADR 0006).
        return self.on_404(request, Http404())

    def on_auth(self, request: HttpRequest, exc: AuthenticationError) -> HttpResponse:
        return self._not_authenticated(request, str(exc))

    def _not_authenticated(self, request: HttpRequest, detail: str) -> HttpResponse:
        response = self._json(
            request, error_body("not_authenticated", detail), HTTPStatus.UNAUTHORIZED
        )
        response["WWW-Authenticate"] = "X-Session-Token"
        return response

    def on_forbidden(
        self, request: HttpRequest, exc: AuthorizationError
    ) -> HttpResponse:
        return self._json(
            request, error_body("permission_denied", str(exc)), HTTPStatus.FORBIDDEN
        )

    def on_django_permission_denied(
        self, request: HttpRequest, exc: PermissionDenied
    ) -> HttpResponse:
        # PermissionDenied("...") keeps its message; a bare one gets the title
        # of Django's default 403 page.
        detail = str(exc) or FORBIDDEN_DETAIL
        return self._json(
            request, error_body("permission_denied", detail), HTTPStatus.FORBIDDEN
        )

    def on_throttled(self, request: HttpRequest, exc: Throttled) -> HttpResponse:
        # When a throttle refuses the request, Operation._check_throttles calls
        # this handler and then sets Retry-After on its response (ninja/operation.py
        # in 1.8.0). Set it here too, the same way, for a Throttled raised directly.
        response = self._json(
            request, error_body("throttled", str(exc)), HTTPStatus.TOO_MANY_REQUESTS
        )
        if exc.wait is not None:
            # Whole seconds, rounded up: Retry-After is integer delta-seconds
            # (RFC 9110), as Ninja's _check_throttles writes it.
            response["Retry-After"] = str(math.ceil(exc.wait))
        return response

    def on_http_error(self, request: HttpRequest, exc: HttpError) -> HttpResponse:
        # BodyModel.get_request_data turns any parser failure into HttpError(400,
        # "Cannot parse request body") raised from it (ninja/params/models.py).
        # The parser's own HttpError, the 415 of core.api.parsers, keeps its
        # status and message.
        cause = exc.__cause__
        error = cause if isinstance(cause, HttpError) else exc
        message = str(error)
        if error.status_code == HTTPStatus.UNAUTHORIZED:
            return self._not_authenticated(request, message)
        code = _http_error_code(error.status_code, message)
        return self._json(request, error_body(code, message), error.status_code)

    def on_unhandled(self, request: HttpRequest, exc: Exception) -> NoReturn:
        # Ninja's default handler answers a text/plain 500 with DEBUG on and
        # re-raises with it off (ninja/errors.py, _default_exception). Re-raise
        # always, so Django answers the same status either way (400 for a
        # SuspiciousOperation such as RequestDataTooBig, 500 otherwise), and
        # with ATOMIC_REQUESTS the request rolls back.
        raise exc


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
    api.exception_handler(Exception)(handlers.on_unhandled)
