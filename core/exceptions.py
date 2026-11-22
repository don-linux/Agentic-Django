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
    See docs/api.md#error-codes.
    """

    status_code: int = HTTPStatus.BAD_REQUEST

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
        super().__init__(message)
        self.message = message
        self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.extra = extra


class ConflictError(ApplicationError):
    """A request that conflicts with the current state of a resource (409)."""

    status_code = HTTPStatus.CONFLICT
