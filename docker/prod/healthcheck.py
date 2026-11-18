# ruff: noqa: INP001
"""Probe the Django readiness endpoint from inside the container.

Exits 0 only when GET /health/ returns 200 and the body ok. Docker stores
the short stderr line in the container health status.

It checks readiness (/health/, which queries the database and pings Valkey), not
liveness (/health/live/): Docker never restarts a container for being unhealthy,
it only reports the status, and "cannot reach the database or the cache" is what
an operator needs to see. Liveness is for orchestrators that restart a failing
container.
"""

from __future__ import annotations

import http.client
import sys
from http import HTTPStatus

_HOST = "127.0.0.1"
_PORT = 8000
_PATH = "/health/"
_TIMEOUT_SECONDS = 3
_OK_BODY = b"ok\n"


def main() -> int:
    client = http.client.HTTPConnection(_HOST, _PORT, timeout=_TIMEOUT_SECONDS)
    try:
        client.request("GET", _PATH)
        response = client.getresponse()
        body = response.read()
    except OSError, http.client.HTTPException:
        sys.stderr.write("connection failed\n")
        return 1
    else:
        if response.status == HTTPStatus.OK and body == _OK_BODY:
            return 0
        sys.stderr.write(f"HTTP {response.status}\n")
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
