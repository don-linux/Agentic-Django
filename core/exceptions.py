"""
Domain errors raised by services.

No API-framework import here: `core.api.exceptions` alone turns these
errors into HTTP responses, with the body from `core.errors`. See
docs/api.md#domain-errors.
"""

from __future__ import annotations

import re
from http import HTTPStatus

_CODE_PATTERN = re.compile(r"[a-z][a-z0-9_]*")


class ApplicationError(Exception):
    """
    A business-rule rejection with a stable, client-facing error code.

    `code` is snake_case and listed in the API's error codes, `message` is the
    English `detail`, and `extra` adds structured context to the response body.
    The status is 409 unless `status_code` says otherwise: 403 for a caller
    who may not, 404 to hide an object, 410 for a removed operation, 412 for a
    stale precondition. Never 400: the request already matched the schema, and
    the contract answers 400 only for one that does not (ADR 0023). See
    docs/api.md#domain-errors.
    """

    status_code: int = HTTPStatus.CONFLICT

    def __init__(
        self,
        message: str,
        *,
        code: str,
        status_code: int | None = None,
        extra: dict[str, object] | None = None,
    ) -> None:
        if not _CODE_PATTERN.fullmatch(code):
            msg = f"Error code {code!r} must be snake_case (^[a-z][a-z0-9_]*$)."
            raise ValueError(msg)
        if status_code == HTTPStatus.BAD_REQUEST:
            msg = (
                "A business rule never answers 400: use 409, 403 or 404 "
                "(docs/api.md#domain-errors)."
            )
            raise ValueError(msg)
        super().__init__(message)
        self.message = message
        self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.extra = extra


class ConflictError(ApplicationError):
    """A request that conflicts with the current state of a resource (409)."""

    status_code = HTTPStatus.CONFLICT
