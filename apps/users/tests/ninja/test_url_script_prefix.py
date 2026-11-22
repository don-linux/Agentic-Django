"""UserOut.url keeps the script prefix the server mounts the project under.

Behind a path prefix (the server's SCRIPT_NAME, or FORCE_SCRIPT_NAME behind a
proxy) WSGIHandler sets Django's script prefix and reverse() includes it, as the
DRF hyperlinked field did. Django's test Client never sets the script prefix, so
these requests go through WSGIHandler, as on a real server.
"""

from __future__ import annotations

import json
import sys
from io import BytesIO
from typing import TYPE_CHECKING

import pytest
from django.conf import settings
from django.core.handlers.wsgi import WSGIHandler
from django.test import override_settings
from django.urls import get_script_prefix
from django.urls import set_script_prefix

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Iterator
    from types import TracebackType

    from django.test import Client

    from apps.users.models import User

# transaction=True: WSGIHandler sends request_started, whose close_old_connections
# receiver closes the connection; the test Client disconnects it, a server does not.
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _restore_script_prefix() -> Iterator[None]:
    # WSGIHandler leaves the prefix in a thread local: give the next test its own.
    prefix = get_script_prefix()
    yield
    set_script_prefix(prefix)


def _me_url(user_client: Client, script_name: str) -> str:
    session = user_client.cookies[settings.SESSION_COOKIE_NAME].value
    environ = {
        "REQUEST_METHOD": "GET",
        "SCRIPT_NAME": script_name,
        "PATH_INFO": "/api/v1/users/me/",
        "QUERY_STRING": "",
        "SERVER_NAME": "testserver",
        "SERVER_PORT": "80",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "HTTP_HOST": "testserver",
        "HTTP_ACCEPT": "application/json",
        "HTTP_COOKIE": f"{settings.SESSION_COOKIE_NAME}={session}",
        "wsgi.input": BytesIO(),
        "wsgi.errors": sys.stderr,
        "wsgi.url_scheme": "http",
        "wsgi.version": (1, 0),
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
    }
    statuses: list[str] = []

    def start_response(
        status: str,
        headers: list[tuple[str, str]],
        exc_info: tuple[type[BaseException], BaseException, TracebackType]
        | tuple[None, None, None]
        | None = None,
        /,
    ) -> Callable[[bytes], object]:
        statuses.append(status)
        return lambda _data: None

    body = b"".join(WSGIHandler()(environ, start_response))
    assert statuses == ["200 OK"]
    return str(json.loads(body)["url"])


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
