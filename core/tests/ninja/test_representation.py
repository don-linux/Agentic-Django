"""docs/api.md#representation on Ninja: field types, UTC datetimes, 201 and 204.

UTCDateTime and ISODate accept RFC 3339 only, the strict types of a body take
no other JSON type (ADR 0022), decimals are strings, a 201 has its Location
from the url field, and a 204 is empty.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from enum import IntEnum
from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Annotated
from typing import Literal

import pytest
from django.http import HttpRequest  # noqa: TC002  # runtime: Ninja signature
from django.http import HttpResponse  # noqa: TC002  # runtime: response argument
from ninja import Query
from ninja import Router
from ninja import Schema
from ninja import Status
from ninja.testing import TestClient
from pydantic import FiniteFloat
from pydantic import StrictBool
from pydantic import StrictFloat
from pydantic import StrictInt

from core.api.schemas import DATE_TIME_PATTERN
from core.api.schemas import BaseFilterSchema
from core.api.schemas import BaseSchema
from core.api.schemas import DecimalString
from core.api.schemas import InputSchema
from core.api.schemas import ISODate
from core.api.schemas import JsonInteger
from core.api.schemas import PublicId
from core.api.schemas import QueryText
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


class _Priority(IntEnum):
    LOW = 1
    HIGH = 2


Amount = Annotated[Decimal, DecimalString(max_digits=6, decimal_places=2)]


class _Typed(InputSchema):
    count: StrictInt
    ratio: StrictFloat
    flag: StrictBool
    at: UTCDateTime
    day: ISODate
    ref: PublicId
    amount: Amount
    priority: Annotated[_Priority, JsonInteger]
    level: Annotated[Literal[1, 2], JsonInteger]


class _Money(BaseSchema):
    amount: Amount


class _Search(BaseFilterSchema):
    name: str | None = None


class _QueryIn(Schema):
    count: int
    flag: bool
    ratio: FiniteFloat
    at: UTCDateTime
    day: ISODate


class _Ok(BaseSchema):
    ok: bool


VALID: dict[str, object] = {
    "count": 1,
    "ratio": 1.5,
    "flag": True,
    "at": "2026-11-13T18:00:00Z",
    "day": "2026-11-13",
    "ref": "0190F2A4-5B6C-7D8E-9F00-112233445566",
    "amount": "1234.50",
    "priority": 2,
    "level": 1,
}


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

    @router.post("/typed/", auth=None, response=_Ok)
    def typed(request: HttpRequest, body: _Typed) -> dict[str, bool]:
        return {"ok": True}

    @router.get("/query/", auth=None, response=_Ok)
    def query(request: HttpRequest, params: Query[_QueryIn]) -> dict[str, bool]:
        return {"ok": True}

    @router.post("/money/", auth=None, response=_Money)
    def money(request: HttpRequest, body: _Money) -> dict[str, Decimal]:
        # A computed Decimal can carry an exponent; the response never does.
        return {"amount": body.amount * Decimal("1E+3")}

    @router.get("/search/", auth=None, response=_Ok)
    def search(
        request: HttpRequest, filters: Query[_Search], q: QueryText | None = None
    ) -> dict[str, bool]:
        return {"ok": True}

    api.add_router("/", router)
    return TestClient(api)


def _post(client: TestClient, path: str, body: dict[str, object]):
    # ninja.testing builds a Mock request: content_type must be set explicitly.
    return client.post(path, json=body, content_type="application/json")


def _field_code(response, field: str) -> str:
    assert response.status_code == HTTPStatus.BAD_REQUEST, response.content
    return response.json()["fields"][field][0]["code"]


def test_a_datetime_without_a_time_zone_is_400(settings: Settings) -> None:
    # RFC 3339 requires the offset; the server does not guess one (ADR 0022).
    settings.TIME_ZONE = "America/Mexico_City"
    response = _post(_client(), "/echo/", {"at": "2026-11-13T18:00:00.123456"})
    assert _field_code(response, "at") == "datetime_parsing"


def test_strict_types_accept_their_own_json_type() -> None:
    response = _post(_client(), "/typed/", VALID)
    assert response.status_code == HTTPStatus.OK, response.content
    # An integer is a number too (JSON Schema "number").
    response = _post(_client(), "/typed/", {**VALID, "ratio": 2})
    assert response.status_code == HTTPStatus.OK, response.content


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("count", "1", "int_type"),
        ("count", True, "int_type"),
        ("count", 1.0, "int_type"),
        ("ratio", "1.5", "float_type"),
        ("ratio", False, "float_type"),
        ("flag", 0, "bool_type"),
        ("flag", "true", "bool_type"),
        ("at", 0, "datetime_type"),
        ("at", "1700000000", "datetime_parsing"),
        ("at", "2026-11-13", "datetime_parsing"),
        ("at", "2026-11-13 18:00:00Z", "datetime_parsing"),
        ("at", "2026-11-13T18:00Z", "datetime_parsing"),
        ("day", 0, "date_type"),
        ("day", "2026-11-13T00:00:00Z", "date_parsing"),
        ("ref", 1, "uuid_type"),
        ("ref", "0190f2a45b6c7d8e9f00112233445566", "uuid_parsing"),
        ("ref", "{0190f2a4-5b6c-7d8e-9f00-112233445566}", "uuid_parsing"),
        ("ref", "urn:uuid:0190f2a4-5b6c-7d8e-9f00-112233445566", "uuid_parsing"),
        ("amount", 12.5, "string_type"),
        ("amount", "12345.00", "string_pattern_mismatch"),
        ("amount", "1.234", "string_pattern_mismatch"),
        ("amount", "1e2", "string_pattern_mismatch"),
        ("amount", " 1.50", "string_pattern_mismatch"),
        ("amount", "1_000", "string_pattern_mismatch"),
        ("amount", "-1.00", "string_pattern_mismatch"),
        ("amount", "NaN", "string_pattern_mismatch"),
        ("priority", True, "int_type"),
        ("priority", "2", "int_type"),
        ("priority", 2.0, "int_type"),
        ("priority", 3, "enum"),
        ("level", True, "int_type"),
        ("level", "1", "int_type"),
        ("level", 3, "literal_error"),
    ],
)
def test_a_body_value_of_another_json_type_is_400(
    field: str, value: object, code: str
) -> None:
    # What Pydantic's lax mode would take, and the schema forbids (ADR 0022).
    response = _post(_client(), "/typed/", {**VALID, field: value})
    assert _field_code(response, field) == code


QUERY = "count=2&flag=true&ratio=1.5&at=2026-11-13T18:00:00Z&day=2026-11-13"


def test_query_parameters_parse_their_text() -> None:
    response = _client().get(f"/query/?{QUERY}")
    assert response.status_code == HTTPStatus.OK, response.content


@pytest.mark.parametrize(
    ("replace", "field", "code"),
    [
        ("at=2026-11-13T18:00:00Z", "at", "datetime_parsing"),
        ("day=2026-11-13", "day", "date_parsing"),
        ("ratio=1.5", "ratio", "finite_number"),
    ],
)
def test_a_query_value_that_is_not_its_type_is_400(
    replace: str, field: str, code: str
) -> None:
    bad = {"at": "at=0.5", "day": "day=0", "ratio": "ratio=nan"}[field]
    response = _client().get(f"/query/?{QUERY.replace(replace, bad)}")
    assert _field_code(response, field) == code


def test_an_offset_input_is_converted_to_utc() -> None:
    response = _post(_client(), "/echo/", {"at": "2026-11-13T12:00:00-06:00"})
    assert response.json()["at"] == "2026-11-13T18:00:00Z"


def test_output_datetimes_render_in_utc_with_z(settings: Settings) -> None:
    settings.TIME_ZONE = "America/Mexico_City"
    response = _client().get("/local/")
    # Microseconds kept (ADR 0021); DjangoJSONEncoder would cut them to ms.
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
    # A request declares the pattern the server checks; a response is any UTC
    # date-time (Ninja publishes them in validation and serialization mode).
    schema = _When.model_json_schema(mode="validation")
    assert schema["properties"]["at"] == {
        "format": "date-time",
        "pattern": f"^{DATE_TIME_PATTERN}$",
        "title": "At",
        "type": "string",
    }
    out = _Out.model_json_schema(mode="serialization")
    assert out["properties"]["at"] == {
        "format": "date-time",
        "title": "At",
        "type": "string",
    }


@pytest.mark.parametrize(
    ("sent", "echoed"),
    [
        # The extremes a request may send: an offset pushes them to years 999
        # and 9999 in UTC, both inside Python's datetime.
        ("1000-01-01T00:00:00+23:59", "0999-12-31T00:01:00Z"),
        ("9998-12-31T23:59:59-23:59", "9999-01-01T23:58:59Z"),
    ],
)
def test_a_request_year_from_1000_to_9998_converts_to_utc(
    sent: str, echoed: str
) -> None:
    response = _post(_client(), "/echo/", {"at": sent})
    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["at"] == echoed


@pytest.mark.parametrize(
    "sent",
    # 0001-01-01T00:00:00+00:01 is year 0 in UTC, which Python's datetime
    # cannot hold: without the bound it was a 500 (OverflowError).
    ["0001-01-01T00:00:00+00:01", "0999-12-31T23:00:00Z", "9999-01-01T00:00:00Z"],
)
def test_a_request_year_outside_1000_to_9998_is_400(sent: str) -> None:
    response = _post(_client(), "/echo/", {"at": sent})
    assert _field_code(response, "at") == "datetime_parsing"


def test_a_decimal_string_keeps_its_digits_and_returns_a_string() -> None:
    # Through a float, 99999999999999999.99 would come back as 1E+17. str()
    # would write the product below as 9.99999E+6.
    response = _post(_client(), "/money/", {"amount": "9999.99"})

    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json() == {"amount": "9999990"}


@pytest.mark.parametrize(
    ("spec", "valid", "invalid"),
    [
        (DecimalString(max_digits=4, decimal_places=2), "12.34", "123.4"),
        (DecimalString(max_digits=2, decimal_places=2), "0.25", "1.25"),
        (DecimalString(max_digits=3, decimal_places=0), "999", "1.5"),
        (DecimalString(max_digits=3, decimal_places=1, negative=True), "-12.5", "+1"),
    ],
)
def test_a_decimal_string_pattern_follows_the_model_field(
    spec: DecimalString, valid: str, invalid: str
) -> None:
    class _One(InputSchema):
        value: Annotated[Decimal, spec]

    assert _One.model_validate({"value": valid}).value == Decimal(valid)
    with pytest.raises(ValueError, match="string_pattern_mismatch"):
        _One.model_validate({"value": invalid})


def test_the_schema_publishes_a_decimal_as_a_string_with_its_pattern() -> None:
    request = _Money.model_json_schema(mode="validation")["properties"]["amount"]
    response = _Money.model_json_schema(mode="serialization")["properties"]["amount"]

    assert (request["type"], request["pattern"]) == (
        "string",
        r"^[0-9]{1,4}(?:\.[0-9]{1,2})?$",
    )
    assert (response["type"], response["pattern"]) == (
        "string",
        r"^-?[0-9]+(?:\.[0-9]+)?$",
    )


def test_an_integer_choice_publishes_its_values() -> None:
    properties = _Typed.model_json_schema()["properties"]

    assert (properties["level"]["type"], properties["level"]["enum"]) == (
        "integer",
        [1, 2],
    )
    assert _Typed.model_json_schema()["$defs"]["_Priority"]["enum"] == [1, 2]


@pytest.mark.parametrize("query", ["name=a%00b", "q=a%00b"])
def test_query_text_refuses_a_nul(query: str) -> None:
    response = _client().get(f"/search/?{query}")
    field = query.split("=", 1)[0]

    assert _field_code(response, field) == "null_characters_not_allowed"


def test_query_text_and_filters_take_plain_text() -> None:
    response = _client().get("/search/?name=Ada&q=Lovelace")

    assert response.status_code == HTTPStatus.OK, response.content
