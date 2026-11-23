"""
The one way to enqueue a Celery task, and the no-op task that proves the wiring.

Services call `enqueue_on_commit(task, **kwargs)`, never `delay()`,
`apply_async()` or `delay_on_commit()`. See docs/tasks.md#writing-a-task.
"""

from __future__ import annotations

import logging
from typing import Protocol

from celery import shared_task
from django.conf import settings
from django.db import transaction
from kombu.exceptions import OperationalError
from kombu.utils.json import dumps

from core.logging import REQUEST_ID_HEADER
from core.middleware import request_id_var

logger = logging.getLogger(__name__)


class PublishableTask(Protocol):
    """What enqueue_on_commit uses of a Celery task: any @shared_task matches."""

    name: str

    def apply_async(
        self, *, kwargs: dict[str, object], headers: dict[str, str]
    ) -> object: ...


def enqueue_on_commit(task: PublishableTask, /, **kwargs: object) -> None:
    """
    Publish `task` with `kwargs` once the current transaction commits.

    Arguments are keyword-only plain JSON. A rollback publishes nothing; a broker
    outage after the commit is logged (task name only), never raised; the request id
    travels in a message header. See docs/tasks.md#writing-a-task.
    """
    try:
        dumps(kwargs)
    except TypeError as error:
        message = (
            f"Arguments of {task.name} must be values Celery's JSON serializer "
            "sends (ids, strings, numbers, UUIDs, dates), not objects."
        )
        raise TypeError(message) from error
    request_id = request_id_var.get()

    def publish() -> None:
        # A closure, not a partial: Django names the callback in its own log when
        # it fails, and a partial's name would print the arguments.
        try:
            task.apply_async(kwargs=kwargs, headers={REQUEST_ID_HEADER: request_id})
        except OperationalError:
            # The broker is unreachable; the data is committed, so log it and
            # let the request succeed.
            logger.exception("could not enqueue task %s", task.name)

    # robust: in production, another failure is logged by Django and does not
    # stop the request's other on_commit callbacks. In eager mode (tests) the
    # task runs inside apply_async, and its own error must reach the test.
    eager = bool(getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False))
    transaction.on_commit(publish, robust=not eager)


@shared_task(name="core.ping", ignore_result=True)
def ping() -> str:
    """Do nothing. Smoke checks and tests use it to prove the wiring end to end."""
    return "pong"
