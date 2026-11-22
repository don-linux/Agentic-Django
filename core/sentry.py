"""
Optional Sentry error reporting, with PII off and credentials scrubbed.

Two filters: EventScrubber (exact names) and scrub_event (before_send), which also
catches longer names and URL credentials. See docs/security.md#error-tracking.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.redis import RedisIntegration
from sentry_sdk.scrubber import DEFAULT_DENYLIST
from sentry_sdk.scrubber import EventScrubber

FILTERED = "[Filtered]"
# Lowercase header names. Add every new custom auth or secret header here.
SENSITIVE_HEADERS = frozenset(
    {
        "x-session-token",
        "x-email-verification-key",
        "x-password-reset-key",
        "authorization",
        "cookie",
        "x-csrftoken",
    },
)
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "password1",
        "password2",
        "current_password",
        "new_password",
        "code",
        "key",
        "token",
        "session_token",
        "secret",
    },
)
# A name that contains one of these holds a secret, in any part of an event.
SENSITIVE_NAME_PARTS = ("password", "token", "secret")
# The credentials of a URL (redis://:<password>@host, https://<user>:<password>@host).
# They end at the last "@" before the path, so an email address in a path stays.
URL_CREDENTIALS = re.compile(r"\b([a-z][a-z0-9+.-]*://)[^\s/?#]*@", re.IGNORECASE)
# Sentry's own bookkeeping, not data: notes about filtered values, keyed like the
# event, and the installed packages (pytokens is a package, not a token).
UNSCRUBBED_SECTIONS = frozenset({"_meta", "modules"})


def scrub_event(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    """
    before_send hook: the last filter before an event leaves the process.

    See docs/security.md#error-tracking for what it filters.
    """
    request = event.get("request")
    if isinstance(request, dict):
        headers = request.get("headers")
        if isinstance(headers, dict):
            for name in headers:
                if name.lower() in SENSITIVE_HEADERS:
                    headers[name] = FILTERED
        if "data" in request:
            request["data"] = _scrub_data(request["data"])
        request.pop("cookies", None)
    for section in event.keys() - UNSCRUBBED_SECTIONS:
        event[section] = _scrub_secrets(event[section])
    return event


def _scrub_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            name: FILTERED if _names_a_secret(name) else _scrub_secrets(item)
            for name, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_scrub_secrets(item) for item in value]
    if isinstance(value, str):
        return URL_CREDENTIALS.sub(rf"\1{FILTERED}@", value)
    return value


def _names_a_secret(name: object) -> bool:
    return isinstance(name, str) and any(
        part in name.lower() for part in SENSITIVE_NAME_PARTS
    )


def _scrub_data(data: Any) -> Any:
    if isinstance(data, dict):
        return {
            key: FILTERED
            if isinstance(key, str) and key.lower() in SENSITIVE_KEYS
            else _scrub_data(value)
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [_scrub_data(item) for item in data]
    return data


def init_sentry(
    *,
    dsn: str,
    environment: str,
    release: str | None,
    traces_sample_rate: float,
) -> None:
    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        traces_sample_rate=traces_sample_rate,
        integrations=[
            DjangoIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
            # Explicit, so the worker reports the same way whatever sentry-sdk
            # detects by itself. No beat monitors: beat is an optional profile.
            CeleryIntegration(monitor_beat_tasks=False),
        ],
        # sentry-sdk turns this on by itself once redis-py is installed, and it
        # reports every cache key even with PII off: a session key is the
        # X-Session-Token credential. See docs/security.md#valkey-access.
        disabled_integrations=[RedisIntegration()],
        # Keep PII off, and never pass data_collection={"queues": True}: it sends
        # the arguments of every task.
        send_default_pii=False,
        before_send=scrub_event,  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
        before_send_transaction=scrub_event,  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
        # EventScrubber compares lowercased keys exactly, so header names must be
        # spelled with hyphens, as they appear in the event.
        event_scrubber=EventScrubber(
            denylist=[*DEFAULT_DENYLIST, *SENSITIVE_KEYS, *SENSITIVE_HEADERS],
            recursive=True,
        ),
    )
