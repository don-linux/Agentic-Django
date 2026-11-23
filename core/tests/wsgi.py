"""Send a request through WSGIHandler, as a real server does.

Django's test Client never sets the script prefix. WSGIHandler does, from
SCRIPT_NAME (or FORCE_SCRIPT_NAME behind a proxy), so a test of what the
server does under a prefix goes through it. The prefix lives in a thread local:
wsgi_request puts back the one it found, so the next test starts clean.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from io import BytesIO
from typing import TYPE_CHECKING
from typing import Any

from django.core.handlers.wsgi import WSGIHandler
from django.urls import get_script_prefix
from django.urls import set_script_prefix

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Mapping
    from types import TracebackType


@dataclass(frozen=True)
class WsgiResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body)


def wsgi_request(
    method: str,
    path: str,
    *,
    script_name: str = "",
    headers: Mapping[str, str] | None = None,
) -> WsgiResponse:
    """Call the project's WSGI handler with a minimal environ and no body."""
    environ: dict[str, object] = {
        "REQUEST_METHOD": method,
        "SCRIPT_NAME": script_name,
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "SERVER_NAME": "testserver",
        "SERVER_PORT": "80",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "HTTP_HOST": "testserver",
        "wsgi.input": BytesIO(),
        "wsgi.errors": sys.stderr,
        "wsgi.url_scheme": "http",
        "wsgi.version": (1, 0),
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
    }
    for name, value in (headers or {}).items():
        environ["HTTP_" + name.upper().replace("-", "_")] = value
    started: list[tuple[str, list[tuple[str, str]]]] = []

    def start_response(
        status: str,
        response_headers: list[tuple[str, str]],
        exc_info: tuple[type[BaseException], BaseException, TracebackType]
        | tuple[None, None, None]
        | None = None,
        /,
    ) -> Callable[[bytes], object]:
        started.append((status, response_headers))
        return lambda _data: None

    prefix = get_script_prefix()
    try:
        body = b"".join(WSGIHandler()(environ, start_response))
    finally:
        set_script_prefix(prefix)
    status, response_headers = started[0]
    return WsgiResponse(int(status.split()[0]), dict(response_headers), body)
