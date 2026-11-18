"""
The one way to enqueue a Celery task, and the no-op task that proves the wiring.

Services call `enqueue_on_commit(task, **kwargs)`, never `delay()`,
`apply_async()` or `delay_on_commit()`. See docs/tasks.md#writing-a-task.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from typing import Any

from celery import shared_task
from django.db import transaction
from kombu.utils.json import dumps

from core.logging import REQUEST_ID_HEADER
from core.middleware import request_id_var

if TYPE_CHECKING:
    from celery import Task

logger = logging.getLogger(__name__)


def enqueue_on_commit(task: Task[Any, Any], /, **kwargs: Any) -> None:
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
            f"Arguments of {task.name} must be plain JSON values (ids, strings, "
            "numbers), not objects."
        )
        raise TypeError(message) from error
    request_id = request_id_var.get()

    def publish() -> None:
        # A closure, not a partial: Django names the callback in its own log when
        # it fails, and a partial's name would print the arguments.
        try:
            task.apply_async(kwargs=kwargs, headers={REQUEST_ID_HEADER: request_id})
        except Exception:
            logger.exception("could not enqueue task %s", task.name)

    transaction.on_commit(publish, robust=True)


@shared_task(name="core.ping", ignore_result=True)
def ping() -> str:
    """Do nothing. Smoke checks and tests use it to prove the wiring end to end."""
    return "pong"
