"""The self-hosted Swagger UI page under the content security policy."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.urls import reverse

from core.tests.html_checks import _INLINE_SCRIPT

if TYPE_CHECKING:
    from django.test import Client

pytestmark = pytest.mark.django_db


def test_swagger_ui_is_self_hosted_without_inline_script(admin_client: Client) -> None:
    response = admin_client.get(reverse("api-docs"))
    html = response.content.decode()

    assert response.status_code == HTTPStatus.OK
    assert "Content-Security-Policy" in response.headers
    assert "/static/drf_spectacular_sidecar/swagger-ui-dist/" in html
    assert "cdn.jsdelivr.net" not in html
    assert _INLINE_SCRIPT.search(html) is None
