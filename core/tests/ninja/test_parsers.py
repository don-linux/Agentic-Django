from __future__ import annotations

from http import HTTPStatus

import pytest
from django.urls import reverse

from core.api.api import api
from core.api.parsers import JSONParser


@pytest.mark.django_db
def test_views_reject_multipart_by_default(user_client) -> None:
    url = reverse("api:user-me")
    response = user_client.patch(url, data={"name": "Ada"})
    assert response.status_code in {
        HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
        HTTPStatus.BAD_REQUEST,
    }


def test_the_default_json_parser_is_the_template_one() -> None:
    assert isinstance(api.parser, JSONParser)
