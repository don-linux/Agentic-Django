from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.urls import reverse

from core.tests.html_checks import _INLINE_SCRIPT

if TYPE_CHECKING:
    from django.http import HttpResponseBase
    from django.test import Client

    from apps.users.models import User

pytestmark = pytest.mark.django_db


def _policy(response: HttpResponseBase) -> dict[str, str]:
    header = response.headers["Content-Security-Policy"]
    return dict(part.strip().split(" ", 1) for part in header.split(";"))


def test_admin_login_is_served_with_the_policy(client: Client) -> None:
    response = client.get(reverse("admin:login"))

    assert response.status_code == HTTPStatus.OK
    policy = _policy(response)
    assert policy["script-src"] == "'self'"
    assert policy["object-src"] == "'none'"
    assert policy["frame-ancestors"] == "'none'"


def test_swagger_init_script_is_a_static_file(admin_client: Client) -> None:
    # The docs page loads Swagger UI's init code from a static file and keeps its
    # settings in a JSON data block, so script-src 'self' needs no inline script.
    # The test settings keep static names as they are; production hashes them,
    # which the docker job's smoke checks (docs/ci.md#docker-smoke).
    response = admin_client.get("/api/v1/docs/")
    html = response.content.decode()

    assert response.status_code == HTTPStatus.OK
    assert '<script src="/static/ninja/swagger-ui-init.js">' in html
    assert '<script type="application/json" id="swagger-settings">' in html
    assert _INLINE_SCRIPT.search(html) is None
    assert _policy(response)["script-src"] == "'self'"


def test_admin_pages_run_no_inline_script(admin_client: Client, user: User) -> None:
    for name, args in (
        ("admin:index", ()),
        ("admin:users_user_changelist", ()),
        ("admin:users_user_change", (user.pk,)),
    ):
        response = admin_client.get(reverse(name, args=args))

        assert response.status_code == HTTPStatus.OK, name
        assert _INLINE_SCRIPT.search(response.content.decode()) is None, name
