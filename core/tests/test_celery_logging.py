"""
A worker logs like the web process, with the task and request ids on each line.

See docs/tasks.md#the-worker-in-production.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING
from typing import Any

import pytest
from celery import shared_task

from config import celery_app
from core.logging import REQUEST_ID_HEADER
from core.logging import JsonFormatter
from core.logging import bind_task
from core.logging import task_var
from core.logging import unbind_task
from core.middleware import NO_REQUEST_ID
from core.middleware import request_id_var
from core.tasks import enqueue_on_commit
from core.tests.production_settings import load_settings

if TYPE_CHECKING:
    from pytest_django.fixtures import DjangoCaptureOnCommitCallbacks

TASK_NAME = "core.tests.log_context"
_SEEN: list[str] = []


@shared_task(name=TASK_NAME)
def _log_context() -> dict[str, Any]:
    """Return what a log line written inside this task would carry."""
    _SEEN.append(request_id_var.get())
    return {"request_id": request_id_var.get(), "task": task_var.get()}


def _record(name: str = "apps.reports.services", **extra: object) -> logging.LogRecord:
    record = logging.LogRecord(name, logging.INFO, __file__, 1, "report done", (), None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_a_task_logs_with_the_request_id_of_its_caller() -> None:
    result = _log_context.apply(headers={REQUEST_ID_HEADER: "req-42"})

    assert result.get() == {"request_id": "req-42", "task": (result.id, TASK_NAME)}
    assert request_id_var.get() == NO_REQUEST_ID
    assert task_var.get() is None


def test_without_a_request_the_task_id_stands_in() -> None:
    # A periodic task, or one enqueued by another task: its lines still share an id.
    result = _log_context.apply()

    assert result.get()["request_id"] == result.id


@pytest.mark.django_db
def test_a_task_enqueued_outside_a_request_does_not_log_the_placeholder(
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    # From a shell or a management command there is no request id to pass on:
    # the placeholder "-" must not replace the task id on the worker's lines.
    _SEEN.clear()
    assert request_id_var.get() == NO_REQUEST_ID

    with django_capture_on_commit_callbacks(execute=True):
        enqueue_on_commit(_log_context)

    assert len(_SEEN) == 1
    assert _SEEN[0] != NO_REQUEST_ID


def test_a_task_run_inside_a_request_gives_the_request_its_id_back() -> None:
    token = request_id_var.set("req-outer")
    try:
        _log_context.apply(headers={REQUEST_ID_HEADER: "req-inner"})

        assert request_id_var.get() == "req-outer"
    finally:
        request_id_var.reset(token)


@pytest.mark.django_db
def test_enqueue_on_commit_hands_the_request_id_to_the_task(
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    _SEEN.clear()
    token = request_id_var.set("req-77")
    try:
        with django_capture_on_commit_callbacks(execute=True):
            enqueue_on_commit(_log_context)
    finally:
        request_id_var.reset(token)

    assert _SEEN == ["req-77"]


def test_unbinding_a_task_that_was_never_bound_changes_nothing() -> None:
    unbind_task(task_id="never-bound")

    assert request_id_var.get() == "-"
    assert task_var.get() is None


def test_a_json_line_written_inside_a_task_names_the_task() -> None:
    bind_task(task_id="task-1", task_name="reports.generate", request_id="req-1")
    try:
        line = json.loads(JsonFormatter().format(_record()))
    finally:
        unbind_task(task_id="task-1")

    assert line["task_id"] == "task-1"
    assert line["task_name"] == "reports.generate"
    assert line["request_id"] == "req-1"


def test_a_json_line_outside_a_task_has_no_task_fields() -> None:
    line = json.loads(JsonFormatter().format(_record()))

    assert "task_id" not in line
    assert "task_name" not in line


def test_the_arguments_celery_attaches_to_its_lines_never_reach_the_log() -> None:
    # celery.app.trace logs "Task ... succeeded" with extra={"data": {...}}, and
    # that dict holds the task's arguments.
    record = _record(
        "celery.app.trace",
        data={"id": "task-1", "name": "reports.generate", "kwargs": "{'code': 'X1'}"},
    )

    formatted = JsonFormatter().format(record)

    assert "data" not in json.loads(formatted)
    assert "X1" not in formatted


def test_other_loggers_keep_an_extra_named_data() -> None:
    line = json.loads(JsonFormatter().format(_record(data={"rows": 3})))

    assert line["data"] == {"rows": 3}


def test_the_worker_keeps_the_root_handler_django_configured() -> None:
    assert celery_app.conf.worker_hijack_root_logger is False


@pytest.mark.parametrize(
    "module",
    ["config.settings.base", "config.settings.local", "config.settings.production"],
)
def test_celery_loggers_write_through_the_console_handler(module: str) -> None:
    # Celery gives celery.task a plain-text handler of its own when it has none,
    # and production.py defines its own LOGGING: both files must declare them.
    loggers = load_settings(module, "LOGGING")["LOGGING"]["loggers"]

    for name in ("celery", "celery.task"):
        assert loggers[name] == {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        }
