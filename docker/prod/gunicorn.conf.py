# ruff: noqa: INP001
"""
Gunicorn settings for the production container.

Every option lives here; start.sh passes only the app and this file. Values that
depend on the server come from DOCKER_PROD_DJANGO_GUNICORN_* (docker/prod/.env).
"""

from __future__ import annotations

import logging
import os
from typing import override


def _int_env(name: str, default: int) -> int:
    # An unset or empty variable keeps the default.
    return int(os.environ.get(name) or default)


# Compose publishes the port on 127.0.0.1 only; the host proxy is the client.
bind = "0.0.0.0:8000"
workers = _int_env("DOCKER_PROD_DJANGO_GUNICORN_WORKERS", 3)
threads = _int_env("DOCKER_PROD_DJANGO_GUNICORN_THREADS", 3)
# With threads (gthread workers) this restarts a worker that stops heartbeating;
# it is not a per-request limit. PostgreSQL's statement timeout bounds each query.
timeout = _int_env("DOCKER_PROD_DJANGO_GUNICORN_TIMEOUT", 30)
# Seconds in-flight requests get after SIGTERM. Keep it below stop_grace_period in
# docker/prod/compose.yaml (30s), or Docker kills workers mid-request.
graceful_timeout = _int_env("DOCKER_PROD_DJANGO_GUNICORN_GRACEFUL_TIMEOUT", 25)
keepalive = 5
# Request line and header limits: gunicorn's own defaults, pinned so an upgrade
# cannot widen them. A longer request line gets 414, and more headers or a longer
# one get 431, before Django runs. The proxy and DATA_UPLOAD_MAX_MEMORY_SIZE cap
# body sizes.
limit_request_line = 4094
limit_request_fields = 100
limit_request_field_size = 8190
# Recycle a worker after about 1000 requests, jittered so they do not restart
# together: caps slow memory growth.
max_requests = 1000
max_requests_jitter = 100
# The worker heartbeat file goes to a tmpfs, not the image's disk.
worker_tmp_dir = "/dev/shm"  # noqa: S108
accesslog = "-"
errorlog = "-"
# No gunicornc control socket: nothing uses it, and it needs a writable $HOME.
control_socket_disable = True

# The peer is always the Docker gateway: log the client from X-Forwarded-For and
# the response's request id. See docs/deploy.md#gunicorn.
access_log_format = (
    '%({x-forwarded-for}i)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s %(D)sus '
    '"%(f)s" "%(a)s" rid=%({x-request-id}o)s'
)


_PROBE_REQUESTS = ('"GET /health/ ', '"GET /health/live/ ')


class SkipHealthAccessLog(logging.Filter):
    """Drop the readiness and liveness probes from the access log."""

    @override
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not any(probe in message for probe in _PROBE_REQUESTS)


def on_starting(_server: object) -> None:
    logging.getLogger("gunicorn.access").addFilter(SkipHealthAccessLog())
