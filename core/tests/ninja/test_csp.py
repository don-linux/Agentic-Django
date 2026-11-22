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


def _directive(policy: str, name: str) -> str:
    return policy.split(name, 1)[1].split(";", 1)[0]


def test_docs_page_sends_a_restrictive_csp(admin_client: Client) -> None:
    response = admin_client.get(reverse("api-docs"))
    assert response.status_code == HTTPStatus.OK
    csp = response.get("Content-Security-Policy", "")
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp


def test_swagger_ui_is_self_hosted_without_inline_script(admin_client: Client) -> None:
    response = admin_client.get(reverse("api-docs"))
    html = response.content.decode()

    assert response.status_code == HTTPStatus.OK
    assert "/static/ninja/swagger-ui-bundle.js" in html
    assert "cdn." not in html
    assert _INLINE_SCRIPT.search(html) is None
    assert "<style" not in html
    policy = response["Content-Security-Policy"]
    assert "'self'" in _directive(policy, "script-src")
    assert "'unsafe-inline'" not in _directive(policy, "script-src")
