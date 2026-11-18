from __future__ import annotations

import json
import logging

from core.logging import JsonFormatter
from core.tests.production_settings import load_settings
from core.tests.production_settings import run_python

FAKE_DSN = "https://public@o0.ingest.sentry.invalid/1"
SENTRY_RELEASE = (
    "import config.settings.production, sentry_sdk\n"
    "print(sentry_sdk.get_client().options['release'])\n"
)


def _record() -> logging.LogRecord:
    return logging.LogRecord("tests.release", logging.INFO, __file__, 1, "hi", (), None)


def test_release_defaults_to_dev() -> None:
    values = load_settings("config.settings.test", "APP_RELEASE", "LOG_FORMATTERS")

    assert values["APP_RELEASE"] == "dev"
    assert values["LOG_FORMATTERS"]["json"]["release"] == "dev"


def test_release_comes_from_the_environment() -> None:
    values = load_settings(
        "config.settings.test",
        "APP_RELEASE",
        "LOG_FORMATTERS",
        env={"APP_RELEASE": "abc1234"},
    )

    assert values["APP_RELEASE"] == "abc1234"
    assert values["LOG_FORMATTERS"]["json"]["release"] == "abc1234"


def test_json_log_lines_carry_the_release() -> None:
    entry = json.loads(JsonFormatter(release="abc1234").format(_record()))

    assert entry["release"] == "abc1234"
    assert "release" not in json.loads(JsonFormatter().format(_record()))


def test_sentry_reports_the_release() -> None:
    result = run_python(
        SENTRY_RELEASE, env={"SENTRY_DSN": FAKE_DSN, "APP_RELEASE": "abc1234"}
    )

    assert result.stdout.strip() == "abc1234"
