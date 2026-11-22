from __future__ import annotations

import re
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from core.middleware import RequestIdMiddleware
from core.middleware import request_id_var

if TYPE_CHECKING:
    from django.http import HttpRequest

GENERATED = re.compile(r"[0-9a-f]{32}")


def _run(
    headers: dict[str, str] | None = None,
) -> tuple[HttpRequest, HttpResponse, str]:
    seen: dict[str, str] = {}

    def view(request: HttpRequest) -> HttpResponse:
        seen["during"] = request_id_var.get()
        return HttpResponse("ok")

    request = RequestFactory().get("/", headers=headers or {})
    response = RequestIdMiddleware(view)(request)
    return request, response, seen["during"]


def test_generates_an_id_when_missing() -> None:
    request, response, during = _run()

    assert GENERATED.fullmatch(response["X-Request-ID"])
    assert request.request_id == response["X-Request-ID"] == during  # type: ignore[attr-defined]  # pyright: ignore[reportAttributeAccessIssue]


def test_keeps_a_valid_incoming_id() -> None:
    _, response, during = _run({"X-Request-ID": "nginx-7f3a.b_2"})

    assert response["X-Request-ID"] == during == "nginx-7f3a.b_2"


@pytest.mark.parametrize("incoming", ["has space", "x" * 129, "semi;colon", ""])
def test_replaces_an_invalid_incoming_id(incoming: str) -> None:
    _, response, _ = _run({"X-Request-ID": incoming})

    assert GENERATED.fullmatch(response["X-Request-ID"])


def test_resets_the_context_after_the_request() -> None:
    _run()

    assert request_id_var.get() == "-"


def test_tags_sentry_events_when_sentry_is_initialized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tags: dict[str, str] = {}
    fake_sentry = SimpleNamespace(
        is_initialized=lambda: True,
        set_tag=lambda key, value: tags.update({key: value}),
    )
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_sentry)

    _, response, _ = _run()

    assert tags == {"request_id": response["X-Request-ID"]}


def test_skips_sentry_when_not_initialized(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_sentry = SimpleNamespace(
        is_initialized=lambda: False,
        set_tag=lambda key, value: pytest.fail("set_tag must not be called"),
    )
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_sentry)

    _run()
