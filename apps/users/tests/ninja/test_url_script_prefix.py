"""UserOut.url keeps the script prefix the server mounts the project under.

Behind a path prefix (the server's SCRIPT_NAME, or FORCE_SCRIPT_NAME behind a
proxy) WSGIHandler sets Django's script prefix and reverse() includes it.
Django's test Client never sets the script prefix, so
these requests go through WSGIHandler, as on a real server.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.conf import settings
from django.test import override_settings

from core.tests.wsgi import wsgi_request

if TYPE_CHECKING:
    from django.test import Client

    from apps.users.models import User

# transaction=True: WSGIHandler sends request_started, whose close_old_connections
# receiver closes the connection; the test Client disconnects it, a server does not.
pytestmark = pytest.mark.django_db(transaction=True)


def _me_url(user_client: Client, script_name: str) -> str:
    session = user_client.cookies[settings.SESSION_COOKIE_NAME].value
    response = wsgi_request(
        "GET",
        "/api/v1/users/me/",
        script_name=script_name,
        headers={
            "Accept": "application/json",
            "Cookie": f"{settings.SESSION_COOKIE_NAME}={session}",
        },
    )
    assert response.status == HTTPStatus.OK
    return str(response.json()["url"])


@pytest.mark.parametrize("script_name", ["/app", "/nested/app"])
def test_the_url_keeps_the_server_script_prefix(
    user: User, user_client: Client, script_name: str
) -> None:
    expected = f"http://testserver{script_name}/api/v1/users/{user.public_id}/"
    assert _me_url(user_client, script_name) == expected


def test_the_url_keeps_force_script_name_behind_a_proxy(
    user: User, user_client: Client
) -> None:
    with override_settings(FORCE_SCRIPT_NAME="/app"):
        url = _me_url(user_client, "")
    assert url == f"http://testserver/app/api/v1/users/{user.public_id}/"


def test_without_a_prefix_the_url_is_unchanged(user: User, user_client: Client) -> None:
    expected = f"http://testserver/api/v1/users/{user.public_id}/"
    assert _me_url(user_client, "") == expected
