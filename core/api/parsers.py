"""JSON parser for Ninja: RecursionError becomes a 400 parse_error."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from typing import Any
from typing import override

from django.core.exceptions import RequestDataTooBig
from django.core.exceptions import TooManyFieldsSent
from ninja.errors import HttpError
from ninja.parser import Parser

if TYPE_CHECKING:
    from django.http import HttpRequest


def _reject_constant(name: str) -> object:
    msg = f"Out of range float values are not JSON compliant: {name!r}"
    raise ValueError(msg)


class JSONParser(Parser):
    """
    Ninja's default json.loads, plus a 400 when JSON is nested too deeply.

    Python's decoder raises RecursionError there, not JSONDecodeError. See
    docs/api.md#error-codes. Source: django-ninja 1.8.0 ninja/parser.py.
    """

    @override
    def parse_body(self, request: HttpRequest) -> dict[str, Any]:
        # Public Parser hook, after authentication and path validation. Ninja
        # deliberately does not inspect Content-Type (guide/parsers/).
        if request.body and (request.content_type or "").lower() != "application/json":
            raw = request.headers.get("content-type", "")
            raise HttpError(415, f'Unsupported media type "{raw}" in request.')
        try:
            # Strict UTF-8 (no BOM) and no NaN/Infinity, as DRF's JSONParser.
            return json.loads(
                request.body.decode("utf-8"), parse_constant=_reject_constant
            )
        except RecursionError as exc:
            raise HttpError(400, "JSON parse error - nested too deeply.") from exc
        except RequestDataTooBig, TooManyFieldsSent:
            raise
        except json.JSONDecodeError as exc:
            raise HttpError(400, f"JSON parse error - {exc}") from exc
