from __future__ import annotations

import logging
import uuid
from contextlib import suppress
from typing import TYPE_CHECKING
from unittest import mock

import pytest
from celery import shared_task
from django.db import transaction
from kombu.exceptions import OperationalError

from config import celery_app
from core.logging import REQUEST_ID_HEADER
from core.middleware import request_id_var
from core.tasks import enqueue_on_commit
from core.tasks import ping
from core.tests.production_settings import run_python

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pytest_django.fixtures import DjangoCaptureOnCommitCallbacks

pytestmark = pytest.mark.django_db

_RECORDED: list[int] = []


@shared_task(name="core.tests.record")
def _record(*, value: int) -> None:
    _RECORDED.append(value)


class _RollbackError(Exception):
    pass


@pytest.fixture
def apply_async() -> Iterator[mock.Mock]:
    """Replace the publish of `ping`, so tests can see how it is called."""
    with mock.patch.object(ping, "apply_async") as patched:
        yield patched


def test_ping_answers_pong() -> None:
    assert ping.apply().get() == "pong"


def test_ping_is_registered_under_an_explicit_name() -> None:
    # @shared_task returns a proxy, so compare what it resolves to.
    assert celery_app.tasks["core.ping"].name == ping.name == "core.ping"
    assert ping.ignore_result is True


def test_tasks_are_discovered_from_the_installed_apps() -> None:
    # Nothing imports core.tasks in this subprocess: only autodiscovery can.
    result = run_python(
        "import django\n"
        "django.setup()\n"
        "from config import celery_app\n"
        "celery_app.loader.import_default_modules()\n"
        "print('core.ping' in celery_app.tasks)\n",
        env={"DJANGO_SETTINGS_MODULE": "config.settings.test"},
    )

    assert result.stdout.strip() == "True"


def test_a_task_is_published_once_after_the_commit(
    apply_async: mock.Mock,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    token = request_id_var.set("req-123")
    try:
        with django_capture_on_commit_callbacks(execute=True):
            enqueue_on_commit(ping, report_id=7)
            # Still inside the transaction: nothing has been sent.
            apply_async.assert_not_called()
    finally:
        request_id_var.reset(token)

    apply_async.assert_called_once_with(
        kwargs={"report_id": 7},
        headers={REQUEST_ID_HEADER: "req-123"},
    )


def test_nothing_is_published_when_the_transaction_rolls_back(
    apply_async: mock.Mock,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    with (
        django_capture_on_commit_callbacks(execute=True) as callbacks,
        suppress(_RollbackError),
        transaction.atomic(),
    ):
        enqueue_on_commit(ping, report_id=7)
        raise _RollbackError

    assert callbacks == []
    apply_async.assert_not_called()


def test_a_broker_outage_is_logged_and_does_not_fail_the_request(
    apply_async: mock.Mock,
    caplog: pytest.LogCaptureFixture,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    apply_async.side_effect = OperationalError("Error 111 connecting to valkey:6379")

    # The data is already committed: the caller must not see an exception.
    with (
        caplog.at_level(logging.ERROR),
        django_capture_on_commit_callbacks(execute=True),
    ):
        enqueue_on_commit(ping, email_hint="private-value")

    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert [(record.name, record.getMessage()) for record in errors] == [
        ("core.tasks", "could not enqueue task core.ping"),
    ]
    # The arguments of a task never reach the log.
    assert "private-value" not in caplog.text


def test_arguments_must_be_plain_json_values(
    apply_async: mock.Mock,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    # Checked when the service enqueues, not later when the broker is reached:
    # in tests tasks run inline and nothing would be serialized.
    with (
        django_capture_on_commit_callbacks(execute=True) as callbacks,
        pytest.raises(TypeError, match="JSON serializer"),
    ):
        enqueue_on_commit(ping, user=object())

    assert callbacks == []
    apply_async.assert_not_called()


def test_ids_and_other_json_values_are_accepted(
    apply_async: mock.Mock,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    public_id = uuid.uuid7()

    with django_capture_on_commit_callbacks(execute=True):
        enqueue_on_commit(ping, public_id=public_id, attempt=1, tags=["a", "b"])

    assert apply_async.call_args.kwargs["kwargs"]["public_id"] == public_id


def test_an_enqueued_task_runs_with_its_keyword_arguments(
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    # The real publish: in tests Celery runs the task inline (eager mode).
    _RECORDED.clear()

    with django_capture_on_commit_callbacks(execute=True):
        enqueue_on_commit(_record, value=7)
        assert _RECORDED == []

    assert _RECORDED == [7]


def test_publishing_gives_up_after_two_quick_retries() -> None:
    assert celery_app.conf.task_publish_retry_policy == {
        "max_retries": 2,
        "interval_start": 0,
        "interval_step": 0.5,
        "interval_max": 1,
    }


@shared_task(name="core.tests.fail", ignore_result=True)
def _fail() -> None:
    msg = "the task's own bug"
    raise ValueError(msg)


def test_in_eager_mode_a_tasks_own_error_reaches_the_caller(
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    # Only a broker outage is logged and swallowed. In tests the task runs
    # inside apply_async, and a bug in it must fail the test (docs/settings.md).
    with (
        pytest.raises(ValueError, match="the task's own bug"),
        django_capture_on_commit_callbacks(execute=True),
    ):
        enqueue_on_commit(_fail)
