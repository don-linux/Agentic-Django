"""
The API's exception handlers: every error is the body of core.errors (ADR 0006).

{code, detail}, plus fields for a validation error and extra for some domain
errors. Ninja's own handlers answer a validation error with 422 and only
{"detail"}; these answer 400 (docs/api.md#error-codes). Each detail comes from
the code that raised the error: Pydantic, Ninja or Django (ADR 0017). See
https://django-ninja.dev/guide/errors/ and django-ninja 1.8.0 ninja/errors.py.
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
from core.errors import NOT_AUTHENTICATED_CODE
from core.errors import NOT_FOUND_CODE
from core.errors import NOT_FOUND_DETAIL
from core.errors import PARSE_ERROR_CODE
from core.errors import PERMISSION_DENIED_CODE
from core.errors import THROTTLED_CODE
from core.errors import UNSUPPORTED_MEDIA_TYPE_CODE
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
# Errors on an object itself, rather than on one of its fields (docs/api.md#error-body).
NON_FIELD_KEY = "non_field_errors"
_EXTRA_FORBIDDEN = "extra_forbidden"
_UNKNOWN_FIELD = "unknown_field"
# The code of a Django ValidationError raised without one.
_INVALID = "invalid"


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


def _branch(tree: dict[str, Any], key: str) -> dict[str, Any]:
    # The errors nested under key. Errors already on key itself (a dict key
    # Pydantic rejected, then its value) move to that branch's non_field_errors.
    current = tree.get(key)
    if isinstance(current, dict):
        return current
    branch: dict[str, Any] = {}
    if isinstance(current, list):
        branch[NON_FIELD_KEY] = current
    tree[key] = branch
    return branch


def _add(tree: dict[str, Any], key: str, item: dict[str, str]) -> None:
    current = tree.get(key)
    if isinstance(current, dict):
        current.setdefault(NON_FIELD_KEY, []).append(item)
    else:
        tree.setdefault(key, []).append(item)


def _ninja_validation_fields(errors: list[dict[str, Any]]) -> dict[str, Any]:
    """Turn Ninja/Pydantic error dicts into the nested {code, detail} tree."""
    fields: dict[str, Any] = {}
    for err in errors:
        loc = _error_path(err.get("loc", ())) or [NON_FIELD_KEY]
        code, msg = _code_and_detail(err)
        tree: dict[str, Any] = fields
        for key in loc[:-1]:
            tree = _branch(tree, key)
        _add(tree, loc[-1], _field_item(msg, code))
    return fields


def _django_validation_fields(exc: DjangoValidationError) -> dict[str, Any]:
    if hasattr(exc, "error_dict"):
        fields: dict[str, Any] = {}
        for field, errors in exc.error_dict.items():
            key = NON_FIELD_KEY if field == NON_FIELD_ERRORS else field
            items = []
            for err in errors:
                message = err.messages[0] if err.messages else str(err)
                items.append(_field_item(message, err.code or _INVALID))
            fields[key] = items
        return fields
    # One item per error, each with its own code (invalid when Django gives none).
    return {
        NON_FIELD_KEY: [
            _field_item(
                err.messages[0] if err.messages else str(err), err.code or _INVALID
            )
            for err in exc.error_list
        ]
    }


# The code of an HttpError that is not a 401, from its status alone
# (docs/api.md#error-codes). Ninja 1.8.0 raises HttpError for a body it cannot
# parse (400) and a failed CSRF check (403), and core.api.parsers raises the
# 415; core/tests/ninja/test_ninja_http_errors.py pins Ninja's raise sites.
_HTTP_ERROR_CODES: dict[int, str] = {
    HTTPStatus.BAD_REQUEST: PARSE_ERROR_CODE,
    HTTPStatus.FORBIDDEN: PERMISSION_DENIED_CODE,
    HTTPStatus.UNSUPPORTED_MEDIA_TYPE: UNSUPPORTED_MEDIA_TYPE_CODE,
}
# Any other status keeps "error"; nothing in Ninja 1.8.0 or the template sends one.
_HTTP_ERROR_FALLBACK = "error"


def _http_error_code(status: int) -> str:
    return _HTTP_ERROR_CODES.get(status, _HTTP_ERROR_FALLBACK)


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
            request, error_body(NOT_FOUND_CODE, NOT_FOUND_DETAIL), HTTPStatus.NOT_FOUND
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
            request,
            error_body(NOT_AUTHENTICATED_CODE, detail),
            HTTPStatus.UNAUTHORIZED,
        )
        response["WWW-Authenticate"] = "X-Session-Token"
        return response

    def on_forbidden(
        self, request: HttpRequest, exc: AuthorizationError
    ) -> HttpResponse:
        return self._json(
            request,
            error_body(PERMISSION_DENIED_CODE, str(exc)),
            HTTPStatus.FORBIDDEN,
        )

    def on_django_permission_denied(
        self, request: HttpRequest, exc: PermissionDenied
    ) -> HttpResponse:
        # PermissionDenied("...") keeps its message; a bare one gets the title
        # of Django's default 403 page.
        detail = str(exc) or FORBIDDEN_DETAIL
        return self._json(
            request, error_body(PERMISSION_DENIED_CODE, detail), HTTPStatus.FORBIDDEN
        )

    def on_throttled(self, request: HttpRequest, exc: Throttled) -> HttpResponse:
        # When a throttle refuses the request, Operation._check_throttles calls
        # this handler and then sets Retry-After on its response (ninja/operation.py
        # in 1.8.0). Set it here too, the same way, for a Throttled raised directly.
        response = self._json(
            request,
            error_body(THROTTLED_CODE, str(exc)),
            HTTPStatus.TOO_MANY_REQUESTS,
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
        code = _http_error_code(error.status_code)
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
