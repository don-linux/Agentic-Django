"""
The Celery app: every CELERY_* setting without the prefix, and the tasks.py of
each installed app. See docs/tasks.md#how-it-is-wired.
"""

import os
from typing import Any

from celery import Celery
from celery.signals import task_postrun
from celery.signals import task_prerun

# Set before the app is built: Celery installs its Django integration only when
# this variable exists. See docs/settings.md#settings-module-default.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

app = Celery("config")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


# The two receivers below import core.logging when a task runs, not here: this
# module loads before Django's settings exist.
@task_prerun.connect
def bind_log_context(task_id: str, task: Any, **kwargs: object) -> None:
    """Give every log line of a task its id, its name and the request id."""
    from core.logging import REQUEST_ID_HEADER  # noqa: PLC0415
    from core.logging import bind_task  # noqa: PLC0415

    headers = task.request.headers or {}
    bind_task(
        task_id=task_id,
        task_name=task.name,
        request_id=headers.get(REQUEST_ID_HEADER),
    )


@task_postrun.connect
def clear_log_context(task_id: str, **kwargs: object) -> None:
    from core.logging import unbind_task  # noqa: PLC0415

    unbind_task(task_id=task_id)
