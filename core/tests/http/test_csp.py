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


def test_swagger_init_script_is_a_separate_request(admin_client: Client) -> None:
    response = admin_client.get(reverse("api-docs") + "?script=")

    assert response.status_code == HTTPStatus.OK
    assert response.headers["Content-Type"].startswith("application/javascript")
    assert b"SwaggerUIBundle" in response.content


def test_admin_pages_run_no_inline_script(admin_client: Client, user: User) -> None:
    for name, args in (
        ("admin:index", ()),
        ("admin:users_user_changelist", ()),
        ("admin:users_user_change", (user.pk,)),
    ):
        response = admin_client.get(reverse(name, args=args))

        assert response.status_code == HTTPStatus.OK, name
        assert _INLINE_SCRIPT.search(response.content.decode()) is None, name
