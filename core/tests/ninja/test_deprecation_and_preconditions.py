"""A deprecated operation announces its end and then answers 410; If-Match is 412.

Through Ninja: the temporal response argument carries the headers, IfMatch is
a required header of the contract, and response_docs documents both.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from http import HTTPStatus

import pytest
from django.http import HttpRequest  # noqa: TC002  # runtime: Ninja signature
from django.http import HttpResponse  # noqa: TC002  # runtime: response argument
from django.utils import timezone
from ninja import Router
from ninja.testing import TestClient

from core.api.deprecation import deprecate
from core.api.responses import GONE_ERRORS
from core.api.responses import LIST_ERRORS
from core.api.responses import LOOKUP_ERRORS
from core.api.responses import PRECONDITION_ERRORS
from core.api.responses import PUBLIC_ERRORS
from core.api.responses import WRITE_ERRORS
from core.api.responses import response_docs
from core.api.schemas import IfMatch  # noqa: TC001  # runtime: Ninja signature
from core.concurrency import check_if_match
from core.concurrency import etag
from core.tests.ninja.support import make_api

SINCE = datetime(2027, 1, 1, tzinfo=UTC)
LINK = "https://example.com/docs/api#legacy"
VERSION = 7
_GONE = {**PUBLIC_ERRORS, **GONE_ERRORS}
_PATCH = {**PUBLIC_ERRORS, **WRITE_ERRORS, **PRECONDITION_ERRORS}


def _client(*, sunset: datetime | None = None) -> TestClient:
    api = make_api(throttle=[])
    router = Router()

    @router.get(
        "/legacy/",
        auth=None,
        response={200: dict[str, bool], **_GONE},
        openapi_extra=response_docs(_GONE),
        deprecated=True,
    )
    def legacy(request: HttpRequest, response: HttpResponse) -> dict[str, bool]:
        deprecate(response, since=SINCE, link=LINK, sunset=sunset)
        return {"ok": True}

    @router.patch(
        "/thing/",
        auth=None,
        response={200: dict[str, bool], **_PATCH},
        openapi_extra=response_docs(_PATCH, etag=True),
    )
    def thing(
        request: HttpRequest, response: HttpResponse, if_match: IfMatch
    ) -> dict[str, bool]:
        check_if_match(if_match=if_match, current=etag(version=VERSION))
        response["ETag"] = etag(version=VERSION + 1)
        return {"ok": True}

    api.add_router("/", router)
    return TestClient(api)


def test_a_deprecated_operation_says_so_in_its_headers() -> None:
    sunset = timezone.now() + timedelta(days=30)
    response = _client(sunset=sunset).get("/legacy/")

    assert response.status_code == HTTPStatus.OK
    assert response["Deprecation"] == f"@{int(SINCE.timestamp())}"
    assert response["Sunset"].endswith(" GMT")
    assert response["Link"] == f'<{LINK}>; rel="deprecation"'


def test_without_a_sunset_there_is_no_sunset_header() -> None:
    response = _client().get("/legacy/")

    assert response.status_code == HTTPStatus.OK
    assert not response.has_header("Sunset")


def test_after_its_sunset_the_operation_is_gone() -> None:
    response = _client(sunset=timezone.now() - timedelta(seconds=1)).get("/legacy/")

    assert response.status_code == HTTPStatus.GONE
    assert response.json()["code"] == "gone"
    assert LINK in response.json()["detail"]


def test_a_write_with_the_current_etag_passes_and_gets_the_new_one() -> None:
    response = _client().patch("/thing/", headers={"If-Match": etag(version=VERSION)})

    assert response.status_code == HTTPStatus.OK
    assert response["ETag"] == etag(version=VERSION + 1)


def test_a_write_with_a_stale_etag_is_412() -> None:
    response = _client().patch("/thing/", headers={"If-Match": '"6"'})

    assert response.status_code == HTTPStatus.PRECONDITION_FAILED
    assert response.json()["code"] == "precondition_failed"


def test_a_write_without_if_match_is_a_400_on_the_header() -> None:
    # The contract requires it, so its absence is a request that does not
    # match the schema, like any other missing parameter.
    response = _client().patch("/thing/")

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["code"] == "validation_error"
    assert list(response.json()["fields"]) == ["If-Match"]


def test_the_schema_publishes_the_deprecation_and_the_precondition() -> None:
    api = make_api(throttle=[])
    router = Router()

    @router.patch(
        "/thing/",
        auth=None,
        response={200: dict[str, bool], **_PATCH},
        openapi_extra=response_docs(_PATCH, etag=True),
        deprecated=True,
    )
    def thing(request: HttpRequest, if_match: IfMatch) -> dict[str, bool]:
        return {"ok": True}

    api.add_router("/", router)
    operation = api.get_openapi_schema(path_prefix="")["paths"]["/thing/"]["patch"]

    assert operation["deprecated"] is True
    [header] = operation["parameters"]
    assert (header["in"], header["name"], header["required"]) == (
        "header",
        "If-Match",
        True,
    )
    assert "ETag" in operation["responses"][200]["headers"]
    assert "content" in operation["responses"][200]
    assert operation["responses"][412]["description"].startswith("Precondition")


@pytest.mark.parametrize(
    ("errors", "expected"),
    [
        (LOOKUP_ERRORS, "Not found, or not visible to the caller."),
        (LIST_ERRORS, "a page past the last one"),
    ],
)
def test_a_list_404_mentions_pages_and_a_lookup_404_does_not(
    errors: dict[int, object], expected: str
) -> None:
    assert expected in response_docs(errors)["responses"][404]["description"]
