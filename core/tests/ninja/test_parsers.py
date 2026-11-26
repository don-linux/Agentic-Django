from __future__ import annotations

from http import HTTPStatus

import pytest
from django.urls import reverse

from core.api.api import api
from core.api.parsers import JSONParser


@pytest.mark.django_db
def test_a_multipart_body_is_415(user_client) -> None:
    response = user_client.patch(reverse("api:user-me"), data={"name": "Ada"})
    assert response.status_code == HTTPStatus.UNSUPPORTED_MEDIA_TYPE
    assert response.json()["code"] == "unsupported_media_type"


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e400", "-1e400"])
def test_the_parser_rejects_non_finite_numbers(rf, value: str) -> None:
    # RFC 8259, section 6: JSON numbers have no NaN or Infinity. 1e400 is a
    # valid JSON number, but too large for a double: it would be infinity.
    request = rf.post("/", data=f'{{"n": {value}}}', content_type="application/json")
    with pytest.raises(ValueError, match="not JSON compliant"):
        JSONParser().parse_body(request)


def test_the_parser_keeps_finite_numbers(rf) -> None:
    # The negative case: a large but finite number and a big integer parse.
    body = '{"n": 1.5e308, "i": 100000000000000000000000}'
    request = rf.post("/", data=body, content_type="application/json")
    assert JSONParser().parse_body(request) == {"n": 1.5e308, "i": 10**23}


@pytest.mark.django_db
def test_an_infinite_number_in_a_body_is_a_parse_error(user_client) -> None:
    response = user_client.patch(
        reverse("api:user-me"), data='{"name": 1e400}', content_type="application/json"
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["code"] == "parse_error"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_the_renderer_refuses_non_finite_numbers(rf, value: float) -> None:
    # json.dumps would write a bare NaN or Infinity token, which is not JSON.
    with pytest.raises(ValueError, match="not JSON compliant"):
        api.renderer.render(rf.get("/"), {"n": value}, response_status=200)


def test_the_default_json_parser_is_the_template_one() -> None:
    assert isinstance(api.parser, JSONParser)
