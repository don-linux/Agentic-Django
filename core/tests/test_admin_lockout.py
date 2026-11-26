from __future__ import annotations

from typing import TYPE_CHECKING

from django.test import RequestFactory
from django.urls import NoReverseMatch

from core.admin_lockout import is_outside_admin

if TYPE_CHECKING:
    import pytest


def test_is_outside_admin_when_the_admin_is_not_routed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_admin(_name: str) -> str:
        msg = "admin:index"
        raise NoReverseMatch(msg)

    monkeypatch.setattr("core.admin_lockout.reverse", no_admin)
    request = RequestFactory().get("/_allauth/app/v1/auth/login")

    assert is_outside_admin(request, {}) is True
