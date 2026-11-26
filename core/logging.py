"""
Logging helpers: the request id on every record and an optional JSON format.

See docs/ARCHITECTURE.md#deployment and docs/settings.md#logging.
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from datetime import UTC
from datetime import datetime
from typing import TYPE_CHECKING
from typing import Any
from typing import override

from django.http import HttpRequest

from core.middleware import NO_REQUEST_ID
from core.middleware import request_id_var
from core.middleware import request_ids
from core.middleware import tag_error_reports

if TYPE_CHECKING:
    from contextvars import Token

# The message header that carries the request id from the web process to the worker.
REQUEST_ID_HEADER = "request_id"
# The Celery task running in this context, as (task id, task name).
task_var: ContextVar[tuple[str, str] | None] = ContextVar("task", default=None)
_task_tokens: dict[str, tuple[Token[str], Token[tuple[str, str] | None]]] = {}

# Attributes every LogRecord has; anything else on a record came from `extra=`.
_STANDARD_ATTRIBUTES = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__,
) | {"message", "asctime", "request_id"}


def bind_task(*, task_id: str, task_name: str, request_id: str | None) -> None:
    """
    Mark the current context as running a task (called when the task starts).

    Its log lines and its Sentry events carry the request id, as a request's
    do; without one, the task id takes its place.
    See docs/ARCHITECTURE.md#deployment.
    """
    if not request_id or request_id == NO_REQUEST_ID:
        request_id = task_id
    _task_tokens[task_id] = (
        request_id_var.set(request_id),
        task_var.set((task_id, task_name)),
    )
    tag_error_reports(request_id)


def unbind_task(*, task_id: str) -> None:
    """Restore what `bind_task` replaced (called when the task ends)."""
    tokens = _task_tokens.pop(task_id, None)
    if tokens is not None:
        request_token, task_token = tokens
        request_id_var.reset(request_token)
        task_var.reset(task_token)


class RequestIdFilter(logging.Filter):
    """Set `record.request_id` from the current request ("-" outside requests)."""

    @override
    def filter(self, record: logging.LogRecord) -> bool:
        request_id = request_id_var.get()
        if request_id == NO_REQUEST_ID:
            # Django logs a 4xx or 5xx response after the middleware returned;
            # the record carries the request (extra={"request": ...}).
            request = getattr(record, "request", None)
            if isinstance(request, HttpRequest):
                request_id = request_ids.get(request, NO_REQUEST_ID)
        record.request_id = request_id
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line, for log aggregators that filter by field."""

    def __init__(self, *args: Any, release: str | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # The image's APP_RELEASE (settings.LOG_FORMATTERS passes it).
        self.release = release

    @override
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", request_id_var.get()),
            "process": record.process,
        }
        if self.release:
            entry["release"] = self.release
        task = task_var.get()
        if task is not None:
            entry["task_id"], entry["task_name"] = task
        if record.exc_info:
            entry["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            entry["stack_info"] = self.formatStack(record.stack_info)
        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRIBUTES or key in entry:
                continue
            if key == "data" and record.name.startswith("celery"):
                # Celery attaches the arguments of the task to its own lines.
                # Arguments never go to the log; the id and the name are above.
                # See docs/settings.md#logging.
                continue
            entry[key] = value
        return json.dumps(entry, default=str)
