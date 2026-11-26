from __future__ import annotations

import logging
import time
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
from core.tests.sockets import hanging_tcp_server

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Iterator

    from pytest_django.fixtures import DjangoCaptureOnCommitCallbacks

pytestmark = pytest.mark.django_db

# The real-broker tests: each publish attempt gives up after one socket
# timeout, so a broker outage does not hold the request that enqueues.
SOCKET_TIMEOUT = 0.2
ENQUEUE_ANSWER_SECONDS = 2
# A broker that accepts and never answers also costs a read timeout per attempt.
HUNG_BROKER_ANSWER_SECONDS = 4 * SOCKET_TIMEOUT + 1.5

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


@pytest.fixture
def real_broker() -> Iterator[Callable[[str], None]]:
    """Publish for real, to the broker URL the test gives; restore the app after."""
    conf = celery_app.conf
    saved = {
        "task_always_eager": conf.task_always_eager,
        "broker_url": conf.broker_url,
        "broker_transport_options": conf.broker_transport_options,
    }

    def point_at(url: str) -> None:
        conf.task_always_eager = False
        conf["broker_url"] = url
        conf.broker_transport_options = {
            "socket_connect_timeout": SOCKET_TIMEOUT,
            "socket_timeout": SOCKET_TIMEOUT,
            "socket_keepalive": True,
        }
        # The production publish retry policy stays: it is what bounds the
        # request (test_publishing_gives_up_after_two_quick_retries).

    # Celery keeps its broker pools from the first publish: the test gets fresh
    # ones for its broker, and the suite gets its own back afterwards.
    with (
        mock.patch.object(celery_app, "_pool", None),
        mock.patch.object(celery_app.amqp, "_producer_pool", None),
    ):
        try:
            yield point_at
        finally:
            for name, value in saved.items():
                setattr(conf, name, value)


def _enqueue_errors(
    caplog: pytest.LogCaptureFixture,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> tuple[list[tuple[str, str]], float]:
    started = time.monotonic()
    with (
        caplog.at_level(logging.ERROR),
        django_capture_on_commit_callbacks(execute=True),
    ):
        # ping takes no arguments: Celery checks them before it contacts the
        # broker.
        enqueue_on_commit(ping)
    elapsed = time.monotonic() - started
    errors = [
        (record.name, record.getMessage())
        for record in caplog.records
        if record.levelno == logging.ERROR
    ]
    return errors, elapsed


def test_enqueue_on_commit_survives_a_closed_broker(
    real_broker: Callable[[str], None],
    caplog: pytest.LogCaptureFixture,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    real_broker("redis://127.0.0.1:6390/7")

    errors, elapsed = _enqueue_errors(caplog, django_capture_on_commit_callbacks)

    assert errors == [("core.tasks", "could not enqueue task core.ping")]
    assert elapsed < ENQUEUE_ANSWER_SECONDS


def test_enqueue_on_commit_is_bounded_when_the_broker_hangs(
    real_broker: Callable[[str], None],
    caplog: pytest.LogCaptureFixture,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    with hanging_tcp_server() as port:
        real_broker(f"redis://127.0.0.1:{port}/7")

        errors, elapsed = _enqueue_errors(caplog, django_capture_on_commit_callbacks)

    assert errors == [("core.tasks", "could not enqueue task core.ping")]
    assert elapsed < HUNG_BROKER_ANSWER_SECONDS
