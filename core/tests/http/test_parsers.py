"""A JSON body nested too deeply is refused through the API."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

from core.tests.payloads import NESTED_TOO_DEEPLY

if TYPE_CHECKING:
    from django.test import Client


def test_patch_me_with_json_nested_too_deeply_is_400(
    user_client: Client,
) -> None:
    user_client.raise_request_exception = False

    response = user_client.patch(
        "/api/v1/users/me/", NESTED_TOO_DEEPLY, content_type="application/json"
    )

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["code"] == "parse_error"
