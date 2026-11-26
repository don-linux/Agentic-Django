from __future__ import annotations

import json
import logging
from typing import Any

import pytest
import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.redis import RedisIntegration
from sentry_sdk.scrubber import EventScrubber

from core import sentry
from core.logging import REQUEST_ID_HEADER
from core.sentry import FILTERED
from core.sentry import scrub_event
from core.tests.guard_values import FAKE_DSN
from core.tests.production_settings import run_python

# Throwaway values that must never reach Sentry.
URL_SECRET = "tests-only-url-secret"  # noqa: S105
TOKEN_SECRET = "tests-only-token-secret"  # noqa: S105
DB_SECRET = "tests-only-db-secret"  # noqa: S105
HOOK_SECRET = "tests-only-hook-secret"  # noqa: S105
SECRETS = (URL_SECRET, TOKEN_SECRET, DB_SECRET, HOOK_SECRET)
BROKER_HOST = "valkey.tests.invalid:6379"
BROKER_URL = f"redis://:{URL_SECRET}@{BROKER_HOST}/1"
MASKED_BROKER_URL = f"redis://{FILTERED}@{BROKER_HOST}/1"
CALLBACK_URL = f"https://hooks:{HOOK_SECRET}@hooks.tests.invalid/done"
MASKED_CALLBACK_URL = f"https://{FILTERED}@hooks.tests.invalid/done"
# The id of the request that enqueued the failing task below.
REQUEST_ID = "tests-request-7"
IS_INITIALIZED = (
    "import config.settings.production, sentry_sdk\n"
    "print(sentry_sdk.is_initialized())\n"
)
REDIS_INTEGRATION_IS_ACTIVE = (
    "import config.settings.production, sentry_sdk\n"
    "from sentry_sdk.integrations.redis import RedisIntegration\n"
    "print(sentry_sdk.get_client().get_integration(RedisIntegration) is not None)\n"
)
# A worker with Sentry on, as production configures it, whose task fails while it
# holds secrets. A request enqueued it, so its message carries the request id.
# The events go to a transport that keeps them instead of sending.
FAILED_TASK_EVENTS = (
    "import json\n"
    "import sentry_sdk\n"
    "from sentry_sdk.transport import Transport\n"
    "\n"
    "class Capture(Transport):\n"
    "    def __init__(self):\n"
    "        super().__init__()\n"
    "        self.events = []\n"
    "    def capture_envelope(self, envelope):\n"
    "        event = envelope.get_event()\n"
    "        if event is not None:\n"
    "            self.events.append(event)\n"
    "\n"
    "capture = Capture()\n"
    "real_init = sentry_sdk.init\n"
    "sentry_sdk.init = lambda **options: real_init(transport=capture, **options)\n"
    "import config.settings.production\n"
    "import django\n"
    "django.setup()\n"
    "from celery import shared_task\n"
    "\n"
    "@shared_task(name='core.tests.failing_report')\n"
    "def failing_report(*, report_id, callback_url):\n"
    "    retries = 3\n"
    f"    api_token = '{TOKEN_SECRET}'\n"
    f"    db_password = '{DB_SECRET}'\n"
    f"    broker_url = '{BROKER_URL}'\n"
    "    raise RuntimeError('cannot reach ' + broker_url)\n"
    "\n"
    "failing_report.apply(\n"
    f"    kwargs=dict(report_id=7, callback_url='{CALLBACK_URL}'),\n"
    f"    headers={{{REQUEST_ID_HEADER!r}: {REQUEST_ID!r}}},\n"
    ")\n"
    "sentry_sdk.flush()\n"
    "print(json.dumps(capture.events, default=str))\n"
)


def _event() -> dict[str, Any]:
    return {
        "request": {
            "headers": {
                "x-session-token": "session-secret",
                "X-Email-Verification-Key": "verify-secret",
                "Accept": "application/json",
            },
            "data": {
                "password": "hunter-two",
                "name": "Ada",
                "steps": [{"code": "123456", "label": "verify"}],
            },
            "cookies": {"sessionid": "cookie-secret"},
        },
    }


def test_scrub_event_filters_credential_headers_in_any_casing() -> None:
    headers = scrub_event(_event(), {})["request"]["headers"]

    assert headers["x-session-token"] == FILTERED
    assert headers["X-Email-Verification-Key"] == FILTERED
    assert headers["Accept"] == "application/json"


def test_scrub_event_filters_sensitive_body_fields_recursively() -> None:
    data = scrub_event(_event(), {})["request"]["data"]

    assert data["password"] == FILTERED
    assert data["steps"][0]["code"] == FILTERED
    assert data["name"] == "Ada"
    assert data["steps"][0]["label"] == "verify"


def test_scrub_event_drops_cookies_and_tolerates_events_without_them() -> None:
    assert "cookies" not in scrub_event(_event(), {})["request"]
    assert scrub_event({"message": "boom"}, {}) == {"message": "boom"}
    without_cookies = {"request": {"headers": {"Accept": "application/json"}}}
    assert scrub_event(without_cookies, {}) == {
        "request": {"headers": {"Accept": "application/json"}},
    }


def _task_event() -> dict[str, Any]:
    """An error event shaped like the one a failed Celery task sends."""
    return {
        "level": "error",
        "logentry": {"message": "retrying %s", "params": [BROKER_URL]},
        "exception": {
            "values": [
                {
                    "type": "RuntimeError",
                    "value": f"cannot reach {BROKER_URL}",
                    "mechanism": {"type": "celery", "handled": False},
                    "stacktrace": {
                        "frames": [
                            {
                                "function": "generate",
                                "context_line": f'    url = "{BROKER_URL}"',
                                "vars": {
                                    "broker_url": repr(BROKER_URL),
                                    "api_token": repr(TOKEN_SECRET),
                                    "DB_PASSWORD": repr(DB_SECRET),
                                    "retries": "3",
                                    "options": {
                                        "clientSecret": repr(TOKEN_SECRET),
                                        "timeout": "5",
                                    },
                                    "attempts": [
                                        {"refresh_token": repr(TOKEN_SECRET)},
                                        "'first'",
                                    ],
                                },
                            },
                        ],
                    },
                },
            ],
        },
        "extra": {
            "celery-job": {
                "task_name": "reports.generate",
                "args": FILTERED,
                "kwargs": FILTERED,
            },
            "sys.argv": ["celery", "--broker", BROKER_URL],
        },
        "breadcrumbs": {
            "values": [{"message": f"connecting to {BROKER_URL}", "data": {}}],
        },
    }


def _task_frame(event: dict[str, Any]) -> dict[str, Any]:
    return event["exception"]["values"][0]["stacktrace"]["frames"][0]


def test_scrub_event_masks_url_credentials_in_every_part_of_a_task_event() -> None:
    event = scrub_event(_task_event(), {})

    frame = _task_frame(event)
    assert frame["vars"]["broker_url"] == repr(MASKED_BROKER_URL)
    assert frame["context_line"] == f'    url = "{MASKED_BROKER_URL}"'
    assert event["exception"]["values"][0]["value"] == (
        f"cannot reach {MASKED_BROKER_URL}"
    )
    assert event["logentry"]["params"] == [MASKED_BROKER_URL]
    assert event["extra"]["sys.argv"] == ["celery", "--broker", MASKED_BROKER_URL]
    assert event["breadcrumbs"]["values"][0]["message"] == (
        f"connecting to {MASKED_BROKER_URL}"
    )


def test_scrub_event_filters_secret_named_variables_and_keeps_the_others() -> None:
    event = scrub_event(_task_event(), {})

    variables = _task_frame(event)["vars"]
    assert variables["api_token"] == FILTERED
    assert variables["DB_PASSWORD"] == FILTERED
    assert variables["options"] == {"clientSecret": FILTERED, "timeout": "5"}
    assert variables["attempts"] == [{"refresh_token": FILTERED}, "'first'"]
    # What is left is still useful to debug the failure.
    assert variables["retries"] == "3"
    assert event["extra"]["celery-job"]["task_name"] == "reports.generate"
    assert event["exception"]["values"][0]["mechanism"]["type"] == "celery"


def test_a_scrubbed_task_event_holds_no_secret_anywhere() -> None:
    raw = json.dumps(_task_event())
    assert all(secret in raw for secret in (URL_SECRET, TOKEN_SECRET, DB_SECRET))

    sent = json.dumps(scrub_event(_task_event(), {}))

    assert [secret for secret in SECRETS if secret in sent] == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (BROKER_URL, MASKED_BROKER_URL),
        (repr(BROKER_URL), repr(MASKED_BROKER_URL)),
        (CALLBACK_URL, MASKED_CALLBACK_URL),
        (
            f"rediss://app:{URL_SECRET}@cache.tests.invalid:6380/0?ssl_cert_reqs=required",
            f"rediss://{FILTERED}@cache.tests.invalid:6380/0?ssl_cert_reqs=required",
        ),
        # A token alone in the user part, without a password.
        (
            f"https://{TOKEN_SECRET}@git.tests.invalid/repo.git",
            f"https://{FILTERED}@git.tests.invalid/repo.git",
        ),
        # A password that contains "@": everything up to the last one goes.
        (
            f"postgres://app:p@ss-{DB_SECRET}@db.tests.invalid:5432/app",
            f"postgres://{FILTERED}@db.tests.invalid:5432/app",
        ),
        (
            f"REDIS://:{URL_SECRET}@{BROKER_HOST}/1",
            f"REDIS://{FILTERED}@{BROKER_HOST}/1",
        ),
        (
            f"primary {BROKER_URL}, replica redis://:{URL_SECRET}@replica.tests.invalid/0",
            f"primary {MASKED_BROKER_URL}, replica redis://{FILTERED}@replica.tests.invalid/0",
        ),
    ],
)
def test_scrub_event_masks_the_credentials_of_a_url(text: str, expected: str) -> None:
    assert scrub_event({"message": text}, {}) == {"message": expected}


@pytest.mark.parametrize(
    "text",
    [
        "redis://valkey.tests.invalid:6379/1",
        "http://localhost:8000/health/",
        # An "@" after the host is not a credential.
        "https://app.tests.invalid/users/ada@tests.invalid",
        "https://app.tests.invalid/search?q=ada@tests.invalid",
        "https://app.tests.invalid/#ada@tests.invalid",
        "ada@tests.invalid wrote to bob@tests.invalid",
        "mailto:ada@tests.invalid",
    ],
)
def test_scrub_event_keeps_text_without_url_credentials(text: str) -> None:
    assert scrub_event({"message": text}, {}) == {"message": text}


@pytest.mark.parametrize(
    "name",
    [
        "api_token",
        "DB_PASSWORD",
        "clientSecret",
        "refresh_token",
        "password_hash",
        "X-Upload-Token",
    ],
)
def test_scrub_event_filters_any_name_that_contains_a_secret_word(name: str) -> None:
    event = {"extra": {name: "leaked", "steps": [{"options": {name: "leaked"}}]}}

    extra = scrub_event(event, {})["extra"]

    assert extra == {name: FILTERED, "steps": [{"options": {name: FILTERED}}]}


def test_scrub_event_keeps_harmless_names_and_values_that_are_not_text() -> None:
    extra = {
        "retries": 3,
        "report_id": "7",
        "broker_host": BROKER_HOST,
        # Close to a secret word, but not one.
        "tokyo": "office",
        "pass_count": 2,
        "enabled": True,
        "nothing": None,
        "ratio": 0.5,
        "pair": ("a", 1),
    }

    scrubbed = scrub_event({"extra": dict(extra)}, {})["extra"]

    assert scrubbed == {**extra, "pair": ["a", 1]}


def test_scrub_event_leaves_the_notes_and_package_list_of_sentry_alone() -> None:
    # _meta mirrors the event to say what was filtered: its "password" entry is a
    # note about a variable, not a value. modules lists the installed packages.
    meta = {"extra": {"password": {"": {"rem": [["!config", "s"]]}}}}
    modules = {"pytokens": "0.3.0", "django": "6.1.3"}

    event = scrub_event({"_meta": meta, "modules": modules, "extra": {}}, {})

    assert event["_meta"] == {"extra": {"password": {"": {"rem": [["!config", "s"]]}}}}
    assert event["modules"] == {"pytokens": "0.3.0", "django": "6.1.3"}


def test_init_sentry_passes_its_arguments_and_scrubs_every_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: captured.update(kw))
    logging_kwargs: dict[str, Any] = {}

    def logging_integration(**kwargs: Any) -> LoggingIntegration:
        logging_kwargs.update(kwargs)
        return LoggingIntegration(**kwargs)

    monkeypatch.setattr(sentry, "LoggingIntegration", logging_integration)

    sentry.init_sentry(
        dsn=FAKE_DSN,
        environment="tests",
        release="abc123",
        traces_sample_rate=0.0,
    )

    assert captured["dsn"] == FAKE_DSN
    assert captured["environment"] == "tests"
    assert captured["release"] == "abc123"
    assert captured["traces_sample_rate"] == 0.0
    assert captured["send_default_pii"] is False
    assert captured["before_send"] is scrub_event
    assert captured["before_send_transaction"] is scrub_event
    # Log records become breadcrumbs from INFO, and events from ERROR.
    assert logging_kwargs == {"level": logging.INFO, "event_level": logging.ERROR}
    scrubber = captured["event_scrubber"]
    assert isinstance(scrubber, EventScrubber)
    event: Any = {"request": {"headers": {"X-Session-Token": "session-secret"}}}
    scrubber.scrub_event(event)
    assert event["request"]["headers"]["X-Session-Token"] != "session-secret"


def _init_kwargs(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: captured.update(kw))
    sentry.init_sentry(
        dsn=FAKE_DSN,
        environment="tests",
        release=None,
        traces_sample_rate=0.0,
    )
    return captured


def test_init_sentry_disables_the_redis_integration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disabled = _init_kwargs(monkeypatch)["disabled_integrations"]

    assert [type(integration) for integration in disabled] == [RedisIntegration]


def test_init_sentry_declares_the_celery_integration_without_beat_monitors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    integrations = _init_kwargs(monkeypatch)["integrations"]

    celery = [item for item in integrations if isinstance(item, CeleryIntegration)]
    assert len(celery) == 1
    assert celery[0].monitor_beat_tasks is False


def test_event_scrubber_filters_secrets_in_task_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scrubber = _init_kwargs(monkeypatch)["event_scrubber"]
    raw_value = "one-time-value"
    event: Any = {
        "extra": {
            "celery-job": {
                "task_name": "reports.generate",
                "kwargs": {"token": raw_value, "report_id": 7},
            },
        },
    }

    scrubber.scrub_event(event)

    kwargs = event["extra"]["celery-job"]["kwargs"]
    assert kwargs["token"] != raw_value
    assert kwargs["report_id"] == 7  # noqa: PLR2004


@pytest.fixture(scope="module")
def failed_task_event() -> dict[str, Any]:
    """The one event Sentry sends for the failed task of FAILED_TASK_EVENTS."""
    result = run_python(FAILED_TASK_EVENTS, env={"SENTRY_DSN": FAKE_DSN})
    [event] = json.loads(result.stdout.splitlines()[-1])
    return event


def test_a_failed_task_sends_no_secret_to_sentry(
    failed_task_event: dict[str, Any],
) -> None:
    event = failed_task_event
    [exception] = event["exception"]["values"]
    frame = exception["stacktrace"]["frames"][-1]
    variables = frame["vars"]
    assert exception["mechanism"]["type"] == "celery"
    assert frame["function"] == "failing_report"
    assert variables["retries"] == "3"
    assert variables["report_id"] == "7"
    assert event["extra"]["celery-job"] == {
        "task_name": "core.tests.failing_report",
        "args": FILTERED,
        "kwargs": FILTERED,
    }
    # Secrets by name, and credentials inside URLs whatever their name.
    assert variables["api_token"] == FILTERED
    assert variables["db_password"] == FILTERED
    assert variables["broker_url"] == repr(MASKED_BROKER_URL)
    assert variables["callback_url"] == repr(MASKED_CALLBACK_URL)
    assert exception["value"] == f"cannot reach {MASKED_BROKER_URL}"
    sent = json.dumps(event)
    assert [secret for secret in SECRETS if secret in sent] == []


def test_a_failed_task_reports_the_request_id_of_its_caller(
    failed_task_event: dict[str, Any],
) -> None:
    # The tag a request's own events carry: the event matches the log lines of
    # the request and of the task (docs/security.md#error-tracking).
    assert failed_task_event["tags"]["request_id"] == REQUEST_ID


def test_production_keeps_the_redis_integration_off() -> None:
    # sentry-sdk enables it by itself as soon as redis-py is installed.
    result = run_python(REDIS_INTEGRATION_IS_ACTIVE, env={"SENTRY_DSN": FAKE_DSN})

    assert result.stdout.strip() == "False"


def test_production_starts_without_sentry_when_dsn_is_empty() -> None:
    assert run_python(IS_INITIALIZED).stdout.strip() == "False"


def test_production_enables_sentry_when_dsn_is_set() -> None:
    result = run_python(IS_INITIALIZED, env={"SENTRY_DSN": FAKE_DSN})

    assert result.stdout.strip() == "True"
