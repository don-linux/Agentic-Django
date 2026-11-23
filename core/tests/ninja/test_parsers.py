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


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_the_parser_rejects_non_finite_numbers(rf, value: str) -> None:
    # RFC 8259, section 6: JSON numbers have no NaN or Infinity.
    request = rf.post("/", data=f'{{"n": {value}}}', content_type="application/json")
    with pytest.raises(ValueError, match="not JSON compliant"):
        JSONParser().parse_body(request)


def test_the_default_json_parser_is_the_template_one() -> None:
    assert isinstance(api.parser, JSONParser)
