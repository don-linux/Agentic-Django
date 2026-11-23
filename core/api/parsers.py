"""JSON parser for Ninja: strict JSON, and 415 for another Content-Type."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from typing import Any
from typing import override

from ninja.errors import HttpError
from ninja.parser import Parser

if TYPE_CHECKING:
    from django.http import HttpRequest


def _reject_constant(name: str) -> object:
    msg = f"Out of range float values are not JSON compliant: {name!r}"
    raise ValueError(msg)


class JSONParser(Parser):
    """
    Ninja's json.loads, strict: UTF-8 only and no NaN or Infinity.

    Any failure (malformed JSON, another encoding, a non-finite number, nesting
    too deep for the decoder) reaches Ninja's BodyModel, which answers
    HttpError(400, "Cannot parse request body") raised from it, the 400
    parse_error. Sources: django-ninja 1.8.0 ninja/parser.py and
    ninja/params/models.py; docs/api.md#error-codes.
    """

    @override
    def parse_body(self, request: HttpRequest) -> dict[str, Any]:
        # Public Parser hook, after authentication and path validation. Ninja
        # deliberately does not inspect Content-Type (guide/parsers/).
        if request.body and (request.content_type or "").lower() != "application/json":
            raw = request.headers.get("content-type", "")
            raise HttpError(415, f'Unsupported media type "{raw}" in request.')
        # RFC 8259: JSON text is UTF-8 (section 8.1, no BOM) and its numbers
        # exclude NaN and Infinity (section 6).
        return json.loads(request.body.decode("utf-8"), parse_constant=_reject_constant)
