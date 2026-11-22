from __future__ import annotations

from http import HTTPStatus

import pytest
from django.urls import reverse


@pytest.mark.django_db
def test_docs_page_sends_a_restrictive_csp(admin_client) -> None:
    response = admin_client.get(reverse("api-docs"))
    assert response.status_code == HTTPStatus.OK
    csp = response.get("Content-Security-Policy", "")
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
