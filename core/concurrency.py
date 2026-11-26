"""
Optimistic concurrency: an ETag per row version, checked against If-Match.

A write that must not overwrite a change it has not seen takes the If-Match
header, and its service checks it inside the transaction, after
select_for_update(). No API-framework import here: services call it. See
docs/api.md#optimistic-concurrency.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from http import HTTPStatus

from django.utils.http import parse_etags

from core.exceptions import ApplicationError

PRECONDITION_FAILED_CODE = "precondition_failed"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def etag(*, version: datetime | int) -> str:
    """
    Return the strong ETag of a row version: its updated_at, or an integer field.

    A datetime counts in microseconds since the epoch, the precision PostgreSQL
    keeps. The tag is strong: If-Match compares strongly (RFC 9110, section
    13.1.1), so a weak tag would never match.
    """
    if isinstance(version, datetime):
        version = (version - _EPOCH) // timedelta(microseconds=1)
    return f'"{version}"'


def check_if_match(*, if_match: str, current: str) -> None:
    """
    Raise 412 precondition_failed unless If-Match names the current ETag.

    `*` matches any current version; a weak tag never matches (RFC 9110,
    section 13.1.1).
    """
    tags = parse_etags(if_match)
    if tags == ["*"] or current in tags:
        return
    msg = "The resource changed since you read it."
    raise ApplicationError(
        msg, code=PRECONDITION_FAILED_CODE, status_code=HTTPStatus.PRECONDITION_FAILED
    )
