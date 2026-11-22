# Background tasks

Celery runs the work that does not fit in a request. It is wired and a worker runs by default, but the template ships no business task: `core.ping` is the only one, a no-op that tests and smoke checks use.

## How it is wired

- `config/celery.py` builds the app from the `CELERY_*` settings in `config/settings/base.py` and discovers the `tasks.py` of each installed app.
- The broker is database 1 of the same Valkey that holds the cache (`VALKEY_BROKER_DB`). Settings build its URL from `VALKEY_URL` and `VALKEY_PASSWORD`; `CELERY_BROKER_URL` replaces it when the broker runs somewhere else.
- There is no result backend. The outcome of a task lives in the model the task updates.
- Services enqueue with `core.tasks.enqueue_on_commit(task, **kwargs)`: the task is published after the commit, and never on a rollback.
- Tests run tasks inline, on an in-memory transport. They never reach a broker.

Tasks and their results are serialized as JSON only (`CELERY_TASK_SERIALIZER`, `CELERY_RESULT_SERIALIZER` and `CELERY_ACCEPT_CONTENT` in `config/settings/base.py`); a project that stores results keeps them JSON too. Django's own task framework (`django.tasks`) is wired to its `DummyBackend`: the default backend would run an accidental `enqueue()` inside the request, while the dummy runs nothing, so the mistake shows. Eager mode and the `memory://` broker exist only in `config/settings/test.py`.

## Writing a task

Add a task only when the work does not fit in a request: a report, an export, bulk email, an outgoing webhook, a call to a slow API. Anything that finishes in well under a second stays in the request.

- **Where.** `apps/<app>/tasks.py`, with `@shared_task` and an explicit `name="<app>.<task>"`. The name is what travels in the message, so moving or renaming the function later breaks nothing. Celery finds the file by itself.
- **Thin.** A task loads rows by id, calls a service and decides about retries. The logic lives in `services.py` and is tested there, without Celery.
- **Arguments.** Keyword arguments, with ids and plain JSON values. Never a model instance, a file or a secret: arguments are stored in Valkey and may reach a log. `enqueue_on_commit` refuses anything that is not JSON.
- **Enqueue.** A service calls `core.tasks.enqueue_on_commit(task, **kwargs)`. The task is published after the commit, so the worker always finds the rows, and never on a rollback. If the broker is down at that moment the error is logged and the request still succeeds, because its data is saved. [Work that must not be lost](#recipe-work-that-must-not-be-lost) needs more than that.
- **Outcome.** Nothing stores what a task returns. The task writes its outcome to the row it works on (a status, a file, an error code), and the client reads that resource.
- **Twice is normal.** A task may run more than once: see [Idempotency](#idempotency).

`tasks.py` and `services.py` import each other: the service enqueues the task, and the task calls a service. Import the module, not the names (`from apps.reports import tasks`, then `tasks.report_generate`), and the cycle is harmless. Ruff forbids the other way out, an import inside the function (`PLC0415`).

In development the `celery_worker` service runs the tasks. It does not reload code, so restart it after changing a task: `docker compose -f docker/dev/compose.yaml restart celery_worker`.

Tests run tasks inline (`config/settings/test.py`). Test the service directly, call the task with `task.apply(kwargs=...)`, and test the enqueue by wrapping the call in `django_capture_on_commit_callbacks(execute=True)`: the task runs when the block ends. With `apply_async` patched, the same block shows one call after a commit and none after a rollback.

Celery's `delay_on_commit` would raise if the broker is down after the commit; `core.tasks.enqueue_on_commit` logs instead, because a 500 would tell the client that data already saved was not.

## Recipe: a report built in the background

The client asks for a report, gets `202 Accepted` at once, and polls the report until it is done. This is a recipe: the template ships no `reports` app.

The model holds the state. `error_code` is a stable value for the client (`timeout`, `unavailable`, `failed`), never the text of an exception:

```python
# apps/reports/models.py
from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models

from core.files import MaxFileSizeValidator
from core.files import UploadTo
from core.files import private_storage
from core.models import BaseModel


class ReportRun(BaseModel):
    """One request for a report, and where its file ends up."""

    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        DONE = "done"
        FAILED = "failed"

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="report_runs",
    )
    month = models.DateField()
    status = models.CharField(max_length=10, choices=Status, default=Status.PENDING)
    # A stable code for the client, never the text of an exception.
    error_code = models.CharField(max_length=50, blank=True)
    file = models.FileField(
        storage=private_storage,
        upload_to=UploadTo("reports/file"),
        validators=[FileExtensionValidator(["csv"]), MaxFileSizeValidator(50_000_000)],
        blank=True,
    )

    def __str__(self) -> str:
        return f"{self.month:%Y-%m} ({self.status})"
```

The services do the work. `report_build` can run twice:

```python
# apps/reports/services.py
from __future__ import annotations

import csv
import io
from typing import TYPE_CHECKING

from django.core.files.base import ContentFile
from django.db import transaction

# The module, not the task: tasks.py imports this file too.
from apps.reports import tasks
from apps.reports.models import ReportRun
from core.tasks import enqueue_on_commit

if TYPE_CHECKING:
    import datetime as dt

    from apps.users.models import User


@transaction.atomic
def report_request(*, user: User, month: dt.date) -> ReportRun:
    report = ReportRun(requested_by=user, month=month)
    report.full_clean()
    report.save()
    # Published after the commit, so the worker always finds the row. The
    # argument is the id: the task loads the rest.
    enqueue_on_commit(tasks.report_generate, report_id=report.pk)
    return report


def report_build(*, report_id: int) -> None:
    """Build the file of a report. A second run finds it done and does nothing."""
    with transaction.atomic():
        report = ReportRun.objects.select_for_update().get(pk=report_id)
        if report.status == ReportRun.Status.DONE:
            return
        # Committed at once, so the client sees "running" while the file is built.
        report.status = ReportRun.Status.RUNNING
        report.error_code = ""
        report.full_clean()
        report.save(update_fields=["status", "error_code"])
    with transaction.atomic():
        # The row stays locked while the file is built: a second delivery of the
        # same task waits here, and then finds the report done.
        report = ReportRun.objects.select_for_update().get(pk=report_id)
        if report.status == ReportRun.Status.DONE:
            return
        report.file.save("report.csv", ContentFile(_render(report)), save=False)
        report.status = ReportRun.Status.DONE
        report.full_clean()
        report.save(update_fields=["file", "status"])


@transaction.atomic
def report_fail(*, report_id: int, error_code: str) -> None:
    """Record that a report could not be built, unless another run finished it."""
    report = ReportRun.objects.select_for_update().get(pk=report_id)
    if report.status == ReportRun.Status.DONE:
        return
    report.status = ReportRun.Status.FAILED
    report.error_code = error_code
    report.full_clean()
    report.save(update_fields=["status", "error_code"])


def _render(report: ReportRun) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["month", "requested_by"])
    # The real query goes here. Read large tables with iterator(chunk_size=2000).
    writer.writerow([f"{report.month:%Y-%m}", report.requested_by.public_id])
    return buffer.getvalue().encode()
```

The task is thin. It retries what may pass, and records what will not:

```python
# apps/reports/tasks.py
from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.db import OperationalError

# The module, not the functions: services.py imports this file too.
from apps.reports import services

if TYPE_CHECKING:
    from celery import Task

# Errors that may be gone on the next try. Never (Exception,).
TRANSIENT_ERRORS = (OperationalError, ConnectionError, TimeoutError)


@shared_task(
    bind=True,
    name="reports.generate",
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
)
def report_generate(self: Task[Any, Any], *, report_id: int) -> None:
    try:
        services.report_build(report_id=report_id)
    except SoftTimeLimitExceeded:
        # CELERY_TASK_SOFT_TIME_LIMIT has passed; the hard limit kills the
        # process soon. Say so while there is time.
        services.report_fail(report_id=report_id, error_code="timeout")
    except TRANSIENT_ERRORS:
        if self.request.retries >= (self.max_retries or 0):
            services.report_fail(report_id=report_id, error_code="unavailable")
        raise
    except Exception:
        # A bug: no retry would fix it. The client must not poll forever.
        services.report_fail(report_id=report_id, error_code="failed")
        raise
```

The view answers `202` with the address to poll:

```python
# apps/reports/api/views.py
from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any
from typing import cast

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.mixins import CreateModelMixin
from rest_framework.mixins import RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from apps.reports.models import ReportRun
from apps.reports.selectors import report_run_list
from apps.reports.services import report_request
from core.api.mixins import PublicIdLookupMixin

from .serializers import ReportRunSerializer

if TYPE_CHECKING:
    from django.db.models import QuerySet
    from rest_framework.request import Request

    from apps.users.models import User


class ReportRunViewSet(
    PublicIdLookupMixin, CreateModelMixin, RetrieveModelMixin, GenericViewSet
):
    serializer_class = ReportRunSerializer
    http_method_names = ["get", "post", "head", "options"]
    queryset = ReportRun.objects.order_by("-id")

    def get_queryset(self) -> QuerySet[ReportRun]:
        if not self.request.user.is_authenticated or self.request.user.pk is None:
            return self.queryset.none()
        return report_run_list(user=self.request.user)

    @extend_schema(responses={status.HTTP_202_ACCEPTED: ReportRunSerializer})
    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        report = report_request(
            user=cast("User", request.user),
            month=serializer.validated_data["month"],
        )
        body = self.get_serializer(report).data
        # 202: accepted, not done. The client polls Location until the status is
        # "done" or "failed".
        return Response(
            body,
            status=status.HTTP_202_ACCEPTED,
            headers={"Location": body["url"]},
        )
```

What the client sees:

```text
POST /api/v1/reports/  {"month": "2026-10-01"}
  202 Accepted, Location: <absolute URL of /api/v1/reports/<id>/>
  {"id": "<id>", "month": "2026-10-01", "status": "pending", "error_code": "", ...}

GET /api/v1/reports/<id>/
  200, "status": "running"   ...and later "done", or "failed" with an error_code
```

The rest is the same as for any resource:

- `ReportRunSerializer` is a `BaseModelSerializer` with `id` from `public_id`, a `url`, and `status`, `error_code` and `created_at` read-only. `report_run_list` is the selector that scopes reports to their owner.
- Add the app to `LOCAL_APPS`, register the viewset in `config/api_router.py`, run `python manage.py makemigrations reports --name create_report_run`, and regenerate `docs/openapi.json`.
- The file is private. Serve it from an action that returns `protected_file_response(report.file, filename="report.csv")` once the status is `done` ([files.md](files.md)).
- Each request starts minutes of work, so give the endpoint a `throttle_scope` ([api.md](api.md#throttling)).
- Tests, besides the usual ones of an endpoint (AGENTS.md, "Testing"): the task is enqueued once after the commit and never after a rollback; building twice keeps the first file; the soft time limit, a bug and the last retry each leave the report `failed`; a failed report can be built again.

## Idempotency

A message is acknowledged when its task ends, not when it starts (`CELERY_TASK_ACKS_LATE`). If the worker dies in the middle, or a deploy stops it, the message is delivered again. And a message that is not acknowledged within an hour (the visibility timeout) goes to another worker while the first one may still be running. So every task must give the same result when it runs twice:

- Lock the row first: `select_for_update()` inside `transaction.atomic()`.
- Return early when the work is already done.
- Keep the lock while doing the work, so a second delivery waits instead of working in parallel, and then returns early.
- When the work calls another system (a payment, a webhook), send it an idempotency key that is the same on every attempt: the row's `public_id`.
- Record progress in a status field, so a client and an operator can see where it stopped.

`report_build` above does all of this. What does not work: checking a flag without a lock, because two workers both read "not done"; or trusting that a message is delivered once.

## Retries

Retry only what may be gone on the next try: the database or another server not answering.

```python
TRANSIENT_ERRORS = (OperationalError, ConnectionError, TimeoutError)


@shared_task(
    name="reports.generate",
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,  # about 1 s, 2 s, 4 s... between tries
    retry_backoff_max=600,  # never more than 10 minutes
    retry_jitter=True,  # a random part, so tasks do not all retry at once
    max_retries=5,
)
```

- Never `autoretry_for=(Exception,)`. A bug would run six times and still fail, and a task that is not idempotent would repeat its side effects each time.
- When the retries run out, record the failure in the row: `report_generate` above checks `self.request.retries`. Otherwise the client polls a report that says `running` forever.
- Never use a `countdown` or `eta` longer than an hour. Valkey delivers the message again every hour until it runs (the visibility timeout), so the task would run several times. For distant work, store the due time in a row and let a [periodic task](#periodic-tasks-beat) pick up what is due.
- A task that fails for good is an ERROR line in the worker's log, with its `task_id`, and an event in Sentry when it is on. Nothing else keeps it: there is no result backend.
- Publishing has its own short retry. `enqueue_on_commit` tries the broker three times within about a second (`CELERY_TASK_PUBLISH_RETRY_POLICY`) and then logs the error, so a broker that refuses connections does not hold a gunicorn thread.

## Recipe: work that must not be lost

`enqueue_on_commit` gives up when the broker is down: it logs the error, and the task never runs. That is right for a report, which the client can ask for again. It is wrong for a charge, or for a webhook a partner depends on. For that work, write an outbox row in the same transaction as the change it belongs to. The row is the record of what is owed, and a periodic task delivers whatever is still pending.

```python
# apps/orders/models.py
from django.db import models

from core.models import BaseModel


class OutboxMessage(BaseModel):
    """Work that must happen after a commit, kept until it has happened."""

    class Status(models.TextChoices):
        PENDING = "pending"
        DONE = "done"

    kind = models.CharField(max_length=50)
    # blank: a message may need no data besides its kind.
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=10, choices=Status, default=Status.PENDING)

    class Meta:
        indexes = [
            # The sweep reads only what is still pending.
            models.Index(
                fields=["created_at"],
                name="orders_outbox_pending_idx",
                condition=models.Q(status="pending"),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} ({self.status})"
```

```python
# apps/orders/services.py
from __future__ import annotations

import datetime as dt
from typing import Any

from django.db import transaction
from django.db.models.functions import Now

# The module, not the tasks: tasks.py imports this file too.
from apps.orders import tasks
from apps.orders.models import OutboxMessage
from core.tasks import enqueue_on_commit

# A message this old was not delivered by the task enqueued with it.
SWEEP_AFTER = dt.timedelta(minutes=2)
SWEEP_BATCH = 100


def outbox_add(*, kind: str, payload: dict[str, Any]) -> OutboxMessage:
    """Record work that must not be lost. Call it inside the write's transaction."""
    message = OutboxMessage(kind=kind, payload=payload)
    message.full_clean()
    message.save()
    # The fast path. If the broker is down the row stays, and the sweep finds it.
    enqueue_on_commit(tasks.outbox_deliver, message_id=message.pk)
    return message


@transaction.atomic
def outbox_deliver(*, message_id: int) -> None:
    message = OutboxMessage.objects.select_for_update().get(pk=message_id)
    if message.status == OutboxMessage.Status.DONE:
        return
    # If this raises, the row stays pending and the sweep tries again.
    _send(message)
    message.status = OutboxMessage.Status.DONE
    message.full_clean()
    message.save(update_fields=["status"])


def outbox_sweep() -> int:
    """Enqueue again what is still pending after SWEEP_AFTER."""
    pending = OutboxMessage.objects.filter(
        status=OutboxMessage.Status.PENDING,
        created_at__lt=Now() - SWEEP_AFTER,
    ).order_by("created_at")
    message_ids = list(pending.values_list("pk", flat=True)[:SWEEP_BATCH])
    for message_id in message_ids:
        enqueue_on_commit(tasks.outbox_deliver, message_id=message_id)
    return len(message_ids)


def _send(message: OutboxMessage) -> None:
    """Do the work: an HTTP POST, a payment capture. Raise when it fails."""
    raise NotImplementedError
```

```python
# apps/orders/tasks.py
from __future__ import annotations

from celery import shared_task

# The module, not the functions: services.py imports this file too.
from apps.orders import services


@shared_task(name="orders.outbox_deliver")
def outbox_deliver(*, message_id: int) -> None:
    services.outbox_deliver(message_id=message_id)


@shared_task(name="orders.outbox_sweep")
def outbox_sweep() -> None:
    services.outbox_sweep()
```

A service records the message together with its own write:

```python
@transaction.atomic
def order_pay(*, order: Order) -> Order:
    order.status = Order.Status.PAID
    order.full_clean()
    order.save(update_fields=["status"])
    outbox_add(kind="order.paid", payload={"order": str(order.public_id)})
    return order
```

The sweep runs every minute (`config/settings/base.py`), and needs the [beat profile](#periodic-tasks-beat):

```python
CELERY_BEAT_SCHEDULE = {
    "orders-outbox-sweep": {"task": "orders.outbox_sweep", "schedule": 60.0},
}
```

- **The row and the change commit together.** A rollback removes both and a commit keeps both, whatever the broker does.
- **The task enqueued with the row is the fast path.** The sweep is the guarantee: it enqueues again whatever is still pending after two minutes.
- **At least once, not exactly once.** If `_send` succeeds and the commit after it fails, the message is sent again. Send the row's `public_id` as an idempotency key, so the receiver can drop the repeat.
- **A send that fails raises.** The transaction rolls back, the row stays pending, and the next sweep tries again. In a real project, count the attempts and alert when a message stays pending for long.
- **Delete what is done** from another periodic task, once it is older than support needs.

## The worker in production

`docker/prod/compose.yaml` has a `celery_worker` service: the same image as `django`, started by `docker/prod/start-worker.sh`. It has no published port.

- **It starts after `django` is healthy**, because `django` applies the migrations. No task runs on an old schema.
- **It is on by default.** Without a worker, an enqueued task waits in the queue forever, without any error. A project that has no tasks yet may stop it:

  ```bash
  docker compose -f docker/prod/compose.yaml up -d --scale celery_worker=0
  ```

- **Health.** Its healthcheck asks the worker for a reply through the broker (`celery inspect ping`), so `docker compose ps` shows whether both are working. Docker reports an unhealthy container; it does not restart it.
- **Logs.** `docker compose -f docker/prod/compose.yaml logs -f celery_worker` prints one JSON object per line, like the web process. A line written while a task runs carries `task_id`, `task_name` and the `request_id` of the request that enqueued it.
- **Stopping.** On `docker stop` (and so on every deploy) the worker lets running tasks finish for 20 seconds (`CELERY_WORKER_SOFT_SHUTDOWN_TIMEOUT`), then returns the unfinished ones to the queue. They run again from the start after the restart, which is why every task must be idempotent.

Useful commands:

```bash
docker compose -f docker/prod/compose.yaml exec celery_worker celery --app config inspect registered
docker compose -f docker/prod/compose.yaml exec celery_worker celery --app config inspect active
```

Sizing, in `docker/prod/.env`:

| Variable | Default | What it sets |
| --- | --- | --- |
| `CELERY_WORKER_CONCURRENCY` | `2` | Worker processes. Each one holds a database connection |
| `CELERY_TASK_SOFT_TIME_LIMIT` | `1500` | Seconds before `SoftTimeLimitExceeded` is raised inside the task |
| `CELERY_TASK_TIME_LIMIT` | `1800` | Seconds before the task's process is killed |
| `CELERY_WORKER_MAX_TASKS_PER_CHILD` | `100` | Tasks a process runs before it is replaced |
| `CELERY_WORKER_MAX_MEMORY_PER_CHILD` | `200000` | Memory (KiB) a process may hold before it is replaced |
| `DOCKER_PROD_CELERY_WORKER_MEM_LIMIT` | `768m` | Memory limit of the container |
| `DOCKER_PROD_CELERY_WORKER_STOP_GRACE_PERIOD` | `60s` | Seconds Docker waits on stop before it kills the container |
| `CELERY_WORKER_LOG_LEVEL` | `INFO` | Log level of the worker |

The soft limit must stay below the hard limit, and both below 3600 seconds: Valkey delivers a message again when it is not acknowledged within an hour (the visibility timeout). Settings refuse to load otherwise.

Settings behind that behaviour (`config/settings/base.py`):

- The broker gives up on a connection after 2 seconds and on a socket operation after 5, longer than the cache's 1 second: a worker can wait, a web request cannot.
- `CELERY_TASK_ACKS_LATE` with `CELERY_TASK_RETRY_ON_WORKER_LOST` (not `task_reject_on_worker_lost`) redelivers a task whose worker died, and `CELERY_WORKER_PREFETCH_MULTIPLIER = 1` keeps each process from reserving tasks it cannot start.
- `CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP` and `CELERY_WORKER_CANCEL_LONG_RUNNING_TASKS_ON_CONNECTION_LOSS` become the defaults in Celery 6; they are set because, left unset, each logs a warning.
- Compose sets `REMAP_SIGTERM=SIGQUIT` on the worker; without it, the tasks a stopping worker had not finished would wait a whole visibility timeout (one hour) before another worker runs them.
- The worker healthcheck starts a Python process, so its interval stays long (60 seconds).
- `start-worker.sh` runs no migrations (the django service applies them) and passes `--quiet`, so the worker's output is log lines only.

## Periodic tasks (beat)

A periodic task is an entry in `CELERY_BEAT_SCHEDULE` (`config/settings/base.py`). The schedule is empty by default, and nothing sends it unless a beat process runs:

```python
from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    "reports-monthly-totals": {
        "task": "reports.monthly_totals",
        # 05:00 on the first day of each month, in TIME_ZONE.
        "schedule": crontab(minute="0", hour="5", day_of_month="1"),
    },
}
```

Beat is an optional compose profile:

```bash
docker compose -f docker/prod/compose.yaml --profile beat up -d
```

- **Run exactly one beat.** It has no lock: two beats send every periodic task twice. Never scale `celery_beat`.
- **Its state does not survive a restart.** The file with the last run of each entry lives in a tmpfs, so after a restart an entry waits for its next due time. A task that must not skip a period reads what is pending from the database instead of trusting the clock.
- **Beat only sends.** The `celery_worker` service runs the task, with the same rules as any other task.
- **Use it for distant work.** A `countdown` or `eta` longer than an hour is delivered again and again (the visibility timeout). Store the due time in a row and let a periodic task pick up what is due.

The host cron that deletes expired sessions (`clearsessions`, [NEXT-STEPS.md](NEXT-STEPS.md)) does not need beat.

Only beat's crontab schedules read `CELERY_TIMEZONE`. The beat service has no healthcheck on purpose.

## Database timeouts in tasks

Requests cancel a query after 30 seconds (`POSTGRES_STATEMENT_TIMEOUT_MS`). The worker gets 300 seconds instead (`CELERY_POSTGRES_STATEMENT_TIMEOUT_MS`), because a report may need one long query. It is never 0: a runaway query would keep its connection forever.

A task that needs more sets the timeout for its own transaction only:

```python
from django.db import connection
from django.db import transaction


def monthly_totals_build(*, month: str) -> None:
    with transaction.atomic():
        with connection.cursor() as cursor:
            # Lasts until this transaction ends. Never 0.
            cursor.execute("SET LOCAL statement_timeout = '15min'")
        ...
```
