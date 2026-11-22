"""docs/api.md#representation on Ninja: UTC datetimes, decimals, 201 and 204.

Restores what core/tests/drf/test_serializers.py and test_create_and_destroy.py
pinned for DRF (UTCDateTimeField, COERCE_DECIMAL_TO_STRING, CreateModelMixin's
Location, DestroyModelMixin's empty 204).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from http import HTTPStatus
from typing import TYPE_CHECKING

from django.http import HttpRequest  # noqa: TC002  # runtime: Ninja signature
from django.http import HttpResponse  # noqa: TC002  # runtime: response argument
from ninja import Router
from ninja import Status
from ninja.testing import TestClient

from core.api.schemas import BaseSchema
from core.api.schemas import InputSchema
from core.api.schemas import UTCDateTime
from core.tests.ninja.support import make_api

if TYPE_CHECKING:
    from pytest_django.fixtures import Settings

SIX_PM_UTC = dt.datetime(2026, 11, 13, 18, 0, 0, 123456, tzinfo=dt.UTC)
ITEM_URL = "http://testserver/api/v1/items/1/"


class _When(InputSchema):
    at: UTCDateTime


class _Out(BaseSchema):
    at: UTCDateTime
    price: Decimal
    url: str


def _client() -> TestClient:
    api = make_api(throttle=[])
    router = Router()

    @router.post("/echo/", auth=None, response=_Out)
    def echo(request: HttpRequest, body: _When) -> dict[str, object]:
        return {"at": body.at, "price": Decimal("12.50"), "url": ITEM_URL}

    @router.get("/local/", auth=None, response=_Out)
    def local(request: HttpRequest) -> dict[str, object]:
        mexico = dt.timezone(dt.timedelta(hours=-6))
        return {
            "at": SIX_PM_UTC.astimezone(mexico),
            "price": Decimal("0.10"),
            "url": "",
        }

    @router.post("/items/", auth=None, response={201: _Out})
    def create(request: HttpRequest, response: HttpResponse) -> Status[_Out]:
        item = _Out(at=SIX_PM_UTC, price=Decimal(1), url=ITEM_URL)
        response["Location"] = item.url
        return Status(HTTPStatus.CREATED, item)

    @router.delete("/items/1/", auth=None, response={204: None})
    def destroy(request: HttpRequest) -> Status[None]:
        return Status(HTTPStatus.NO_CONTENT, None)

    api.add_router("/", router)
    return TestClient(api)


def _post(client: TestClient, path: str, body: dict[str, str]):
    # ninja.testing builds a Mock request: content_type must be set explicitly.
    return client.post(path, json=body, content_type="application/json")


def test_naive_input_is_read_as_utc(settings: Settings) -> None:
    settings.TIME_ZONE = "America/Mexico_City"
    response = _post(_client(), "/echo/", {"at": "2026-11-13T18:00:00.123456"})
    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["at"] == "2026-11-13T18:00:00.123456Z"


def test_an_offset_input_is_converted_to_utc() -> None:
    response = _post(_client(), "/echo/", {"at": "2026-11-13T12:00:00-06:00"})
    assert response.json()["at"] == "2026-11-13T18:00:00Z"


def test_output_datetimes_render_in_utc_with_z(settings: Settings) -> None:
    settings.TIME_ZONE = "America/Mexico_City"
    response = _client().get("/local/")
    # DRF's ISO_8601 keeps microseconds; DjangoJSONEncoder would cut them to ms.
    assert response.json()["at"] == "2026-11-13T18:00:00.123456Z"


def test_decimals_are_strings() -> None:
    response = _post(_client(), "/echo/", {"at": "2026-11-13T18:00:00Z"})
    assert response.json()["price"] == "12.50"


def test_create_is_201_with_location_at_the_url_field() -> None:
    response = _client().post("/items/")
    assert response.status_code == HTTPStatus.CREATED
    assert response["Location"] == ITEM_URL == response.json()["url"]


def test_destroy_is_204_with_an_empty_body() -> None:
    response = _client().delete("/items/1/")
    assert response.status_code == HTTPStatus.NO_CONTENT
    assert response.content == b""


def test_utc_datetime_is_a_date_time_string_in_the_schema() -> None:
    schema = _When.model_json_schema()
    assert schema["properties"]["at"] == {
        "format": "date-time",
        "title": "At",
        "type": "string",
    }
    out = _Out.model_json_schema(mode="serialization")
    assert out["properties"]["at"]["format"] == "date-time"
