"""An ETag names a row version, and If-Match must name the current one."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from http import HTTPStatus

import pytest

from core.concurrency import check_if_match
from core.concurrency import etag
from core.exceptions import ApplicationError

SAVED = datetime(2026, 11, 23, 18, 0, 0, 123456, tzinfo=UTC)


def test_an_etag_counts_microseconds_or_takes_the_version_field() -> None:
    seconds = int(SAVED.replace(microsecond=0).timestamp())
    assert etag(version=SAVED) == f'"{seconds * 1_000_000 + 123456}"'
    assert etag(version=7) == '"7"'


@pytest.mark.parametrize("if_match", ['"7"', '"6", "7"', "*", ' "7" ', '"6","7"'])
def test_if_match_passes_when_it_names_the_current_version(if_match: str) -> None:
    check_if_match(if_match=if_match, current=etag(version=7))


@pytest.mark.parametrize("if_match", ['"6"', 'W/"7"', "7", "", '"7'])
def test_any_other_if_match_is_a_412(if_match: str) -> None:
    # A weak tag never matches: If-Match compares strongly (RFC 9110, 13.1.1).
    with pytest.raises(ApplicationError) as excinfo:
        check_if_match(if_match=if_match, current=etag(version=7))

    assert excinfo.value.status_code == HTTPStatus.PRECONDITION_FAILED
    assert excinfo.value.code == "precondition_failed"
