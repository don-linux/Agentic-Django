from __future__ import annotations

from typing import IO
from typing import TYPE_CHECKING
from typing import Any

from rest_framework import parsers
from rest_framework.exceptions import ParseError

if TYPE_CHECKING:
    from collections.abc import Mapping


class JSONParser(parsers.JSONParser):
    """
    DRF's JSONParser, plus a 400 for JSON nested too deeply to decode.

    Python's decoder raises RecursionError there, not the ValueError DRF turns
    into parse_error. See docs/api.md#error-codes.
    """

    def parse(
        self,
        stream: IO[Any],
        media_type: str | None = None,
        parser_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            return super().parse(stream, media_type, parser_context)
        except RecursionError as exc:
            msg = "JSON parse error - nested too deeply."
            raise ParseError(msg) from exc
