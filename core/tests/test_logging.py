from __future__ import annotations

import importlib.util
import json
import logging
import sys
import uuid
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING
from typing import override

import pytest

from core.logging import JsonFormatter
from core.logging import RequestIdFilter
from core.middleware import NO_REQUEST_ID
from core.middleware import request_id_var

if TYPE_CHECKING:
    from types import ModuleType

    from django.test import Client

GUNICORN_CONF = Path(__file__).resolve().parents[2] / "docker/prod/gunicorn.conf.py"


def _record(*, with_exception: bool = False) -> logging.LogRecord:
    exc_info = None
    if with_exception:
        try:
            msg = "boom"
            raise ValueError(msg)  # noqa: TRY301
        except ValueError:
            exc_info = sys.exc_info()
    record = logging.LogRecord(
        name="tests.logging",
        level=logging.ERROR if with_exception else logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=exc_info,
    )
    record.user_id = 7
    return record


def _with_request_id(record: logging.LogRecord, request_id: str) -> logging.LogRecord:
    token = request_id_var.set(request_id)
    try:
        RequestIdFilter().filter(record)
    finally:
        request_id_var.reset(token)
    return record


# The filter adds an attribute that LogRecord does not declare; a format string
# reads it the way the configured formats do ("[%(request_id)s]").
REQUEST_ID_FORMAT = logging.Formatter("%(request_id)s")


def test_filter_adds_the_current_request_id() -> None:
    record = _with_request_id(_record(), "abc123")

    assert REQUEST_ID_FORMAT.format(record) == "abc123"


def test_filter_outside_a_request_uses_a_dash() -> None:
    record = _record()

    # A filter that returns False drops the record; this one only adds the id.
    assert RequestIdFilter().filter(record) is True
    assert REQUEST_ID_FORMAT.format(record) == "-"


def test_json_formatter_writes_one_object_with_request_id_and_extra() -> None:
    entry = json.loads(JsonFormatter().format(_with_request_id(_record(), "abc123")))

    assert entry["message"] == "hello world"
    assert entry["level"] == "INFO"
    assert entry["logger"] == "tests.logging"
    assert entry["request_id"] == "abc123"
    assert entry["user_id"] == 7  # noqa: PLR2004
    assert entry["timestamp"].endswith("Z")
    assert "exc_info" not in entry


def test_json_formatter_includes_the_traceback() -> None:
    entry = json.loads(JsonFormatter().format(_record(with_exception=True)))

    assert "ValueError: boom" in entry["exc_info"]


def test_json_formatter_includes_the_process_and_the_stack() -> None:
    record = _record()
    record.stack_info = "Stack (most recent call last):"

    entry = json.loads(JsonFormatter().format(record))

    assert entry["process"] == record.process
    assert entry["stack_info"] == "Stack (most recent call last):"


def test_json_formatter_writes_an_extra_json_cannot_hold_as_text() -> None:
    record = _record()
    record.public_id = uuid.UUID(int=0)

    entry = json.loads(JsonFormatter().format(record))

    assert entry["public_id"] == "00000000-0000-0000-0000-000000000000"


def _gunicorn_conf() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gunicorn_conf", GUNICORN_CONF)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gunicorn_access_log_drops_the_health_probe() -> None:
    conf = _gunicorn_conf()
    skip = conf.SkipHealthAccessLog()

    def line(request: str) -> logging.LogRecord:
        return logging.LogRecord(
            "gunicorn.access", logging.INFO, "", 0, request, (), None
        )

    assert not skip.filter(line('203.0.113.7 - - [t] "GET /health/ HTTP/1.1" 200'))
    assert not skip.filter(line('203.0.113.7 - - [t] "GET /health/live/ HTTP/1.1" 200'))
    assert skip.filter(line('203.0.113.7 - - [t] "GET /api/v1/users/me/ HTTP/1.1" 200'))


def test_gunicorn_access_log_uses_client_ip_and_response_request_id() -> None:
    access_log_format = _gunicorn_conf().access_log_format

    assert access_log_format.startswith("%({x-forwarded-for}i)s ")
    assert "rid=%({x-request-id}o)s" in access_log_format


_GUNICORN_ENV = (
    "DOCKER_PROD_DJANGO_GUNICORN_WORKERS",
    "DOCKER_PROD_DJANGO_GUNICORN_THREADS",
    "DOCKER_PROD_DJANGO_GUNICORN_TIMEOUT",
    "DOCKER_PROD_DJANGO_GUNICORN_GRACEFUL_TIMEOUT",
)


def test_gunicorn_conf_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _GUNICORN_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DOCKER_PROD_DJANGO_GUNICORN_TIMEOUT", "")  # empty = default

    conf = _gunicorn_conf()

    assert (conf.workers, conf.threads) == (3, 3)
    assert (conf.timeout, conf.graceful_timeout) == (30, 25)
    assert (conf.max_requests, conf.max_requests_jitter) == (1000, 100)
    assert conf.keepalive == 5  # noqa: PLR2004
    assert conf.bind.endswith(":8000")
    assert conf.worker_tmp_dir == "/dev/shm"  # noqa: S108
    assert (conf.accesslog, conf.errorlog) == ("-", "-")
    assert conf.control_socket_disable is True
    # gunicorn's own defaults, pinned against a future change.
    assert (
        conf.limit_request_line,
        conf.limit_request_fields,
        conf.limit_request_field_size,
    ) == (4094, 100, 8190)


def test_gunicorn_conf_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in zip(_GUNICORN_ENV, ("5", "2", "60", "40"), strict=True):
        monkeypatch.setenv(name, value)

    conf = _gunicorn_conf()

    assert (conf.workers, conf.threads, conf.timeout, conf.graceful_timeout) == (
        5,
        2,
        60,
        40,
    )


class _Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    @override
    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.mark.django_db
def test_a_response_django_logs_keeps_the_request_id(client: Client) -> None:
    # Django logs a 4xx (django.request) after the middleware chain returned
    # and RequestIdMiddleware reset request_id_var; the record carries the
    # request, and the filter takes the id from it.
    request_id = uuid.uuid4().hex
    handler = _Collect()
    logger = logging.getLogger("django.request")
    logger.addHandler(handler)
    try:
        response = client.get("/api/v1/users/me/", headers={"X-Request-ID": request_id})
    finally:
        logger.removeHandler(handler)
    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert request_id_var.get() == NO_REQUEST_ID
    (record,) = handler.records
    assert RequestIdFilter().filter(record)
    assert getattr(record, "request_id", None) == request_id
