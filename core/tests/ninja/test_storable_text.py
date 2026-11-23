"""Every input schema refuses text PostgreSQL cannot store, at any depth.

A NUL (null_characters_not_allowed, Django's code and message) or a lone
surrogate (Pydantic's string_unicode) would otherwise be a 500 when the row is
saved: psycopg refuses the first and cannot encode the second. The conformance
test never generates either (allow-x00 is off, and Hypothesis sends no
surrogates), so these tests send them.
"""

from __future__ import annotations

from http import HTTPStatus

import pytest
from django.http import HttpRequest  # noqa: TC002  # runtime: Ninja signature
from ninja import Router
from ninja.testing import TestClient
from pydantic_core import PydanticCustomError

from core.api.schemas import BaseSchema
from core.api.schemas import InputSchema
from core.api.schemas import storable_text
from core.tests.ninja.support import make_api

NUL = {
    "code": "null_characters_not_allowed",
    "detail": "Null characters are not allowed.",
}
SURROGATE = "\ud800"


class _Child(InputSchema):
    note: str


class _Text(InputSchema):
    note: str = ""
    tags: list[str] = []
    labels: dict[str, str] = {}
    child: _Child | None = None


class _Ok(BaseSchema):
    ok: bool


def _client() -> TestClient:
    api = make_api(throttle=[])
    router = Router()

    @router.post("/text/", auth=None, response=_Ok)
    def text(request: HttpRequest, body: _Text) -> dict[str, bool]:
        return {"ok": True}

    api.add_router("/", router)
    return TestClient(api)


def _post(body: dict[str, object]):
    # ninja.testing builds a Mock request: content_type must be set explicitly.
    return _client().post("/text/", json=body, content_type="application/json")


def _errors(response, field: str) -> list[dict[str, str]]:
    assert response.status_code == HTTPStatus.BAD_REQUEST, response.content
    errors = response.json()["fields"][field]
    # A nested schema's errors nest under its field (docs/api.md#error-body).
    return errors["note"] if field == "child" else errors


def test_text_without_nul_or_surrogates_is_accepted() -> None:
    body: dict[str, object] = {
        "note": "é ✓",
        "tags": ["a"],
        "labels": {"k": "v"},
        "child": {"note": "x"},
    }
    response = _post(body)
    assert response.status_code == HTTPStatus.OK, response.content


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"note": "a\x00b"}, "note"),
        ({"tags": ["ok", "a\x00"]}, "tags"),
        ({"labels": {"k\x00": "v"}}, "labels"),
        ({"labels": {"k": "\x00"}}, "labels"),
        ({"child": {"note": "\x00"}}, "child"),
    ],
)
def test_a_nul_is_400_wherever_it_is(body: dict[str, object], field: str) -> None:
    assert _errors(_post(body), field) == [NUL]


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"note": SURROGATE}, "note"),
        ({"tags": [SURROGATE]}, "tags"),
        ({"child": {"note": SURROGATE}}, "child"),
    ],
)
def test_a_lone_surrogate_is_400(body: dict[str, object], field: str) -> None:
    # json.dumps writes it as the escape \ud800, which JSON allows.
    assert _errors(_post(body), field)[0]["code"] == "string_unicode"


def test_deep_nesting_is_walked_without_recursion() -> None:
    # A JSON body nested thousands deep decodes fine; the check must not be
    # the thing that fails with RecursionError (a 500).
    deep: object = "ok"
    for _ in range(10_000):
        deep = [deep]
    assert storable_text(deep) is deep
    bad: object = "a\x00b"
    for _ in range(10_000):
        bad = {"k": bad}
    with pytest.raises(PydanticCustomError):
        storable_text(bad)
