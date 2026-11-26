"""
Deprecation headers for an operation that a replacement on /api/v1/ supersedes.

The operation also sets deprecated=True on its route decorator, so the schema
says it, and answers 410 gone once its sunset date has passed. See
docs/api.md#deprecating-endpoints-and-fields.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from email.utils import format_datetime
from http import HTTPStatus
from typing import TYPE_CHECKING

from django.utils import timezone

from core.exceptions import ApplicationError

if TYPE_CHECKING:
    from django.http import HttpResponse

GONE_CODE = "gone"


def deprecate(
    response: HttpResponse,
    *,
    since: datetime,
    link: str,
    sunset: datetime | None = None,
) -> None:
    """
    Add the Deprecation, Sunset and Link headers (RFC 9745, RFC 8594).

    Call it from the operation, with Ninja's temporal `response` argument.
    After `sunset`, raise 410 gone instead: the operation was removed.
    """
    if sunset is not None and timezone.now() >= sunset:
        msg = f"This operation was removed on {sunset.date().isoformat()}; see {link}."
        raise ApplicationError(msg, code=GONE_CODE, status_code=HTTPStatus.GONE)
    response["Deprecation"] = f"@{int(since.timestamp())}"
    if sunset is not None:
        response["Sunset"] = format_datetime(sunset.astimezone(UTC), usegmt=True)
    response["Link"] = f'<{link}>; rel="deprecation"'
