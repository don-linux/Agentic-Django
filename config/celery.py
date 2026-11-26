"""
The Celery app: every CELERY_* setting without the prefix, and the tasks.py of
each installed app. See docs/tasks.md#how-it-is-wired.
"""

import os
from typing import Any

from celery import Celery
from celery.signals import task_postrun
from celery.signals import task_prerun

from core.logging import REQUEST_ID_HEADER
from core.logging import bind_task
from core.logging import unbind_task

# Set before the app is built: Celery installs its Django integration only when
# this variable exists. See docs/settings.md#settings-module-default.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

app = Celery("config")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@task_prerun.connect
def bind_log_context(task_id: str, task: Any, **kwargs: object) -> None:
    """Give every log line and Sentry event of a task its ids and its name."""
    headers = task.request.headers or {}
    bind_task(
        task_id=task_id,
        task_name=task.name,
        request_id=headers.get(REQUEST_ID_HEADER),
    )


@task_postrun.connect
def clear_log_context(task_id: str, **kwargs: object) -> None:
    unbind_task(task_id=task_id)
