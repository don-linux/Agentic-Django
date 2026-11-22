"""JSON parser for Ninja: RecursionError becomes a 400 parse_error."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from typing import Any

from django.core.exceptions import RequestDataTooBig
from django.core.exceptions import TooManyFieldsSent
from ninja.errors import HttpError
from ninja.parser import Parser

if TYPE_CHECKING:
    from django.http import HttpRequest


class JSONParser(Parser):
    """
    Ninja's default json.loads, plus a 400 when JSON is nested too deeply.

    Python's decoder raises RecursionError there, not JSONDecodeError. See
    docs/api.md#error-codes. Source: django-ninja 1.8.0 ninja/parser.py.
    """

    def parse_body(self, request: HttpRequest) -> dict[str, Any]:
        try:
            return super().parse_body(request)
        except RecursionError as exc:
            raise HttpError(400, "JSON parse error - nested too deeply.") from exc
        except RequestDataTooBig, TooManyFieldsSent:
            raise
        except json.JSONDecodeError as exc:
            raise HttpError(400, f"JSON parse error - {exc}") from exc
