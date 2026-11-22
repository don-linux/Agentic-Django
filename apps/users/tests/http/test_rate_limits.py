"""Per-address caps on allauth's email-sending rate limits (cost and abuse)."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from allauth.account import app_settings as account_settings
from django.conf import settings as django_settings
from django.core import mail

from apps.users.tests.factories import UserFactory

if TYPE_CHECKING:
    from django.test import Client
    from pytest_django.fixtures import Settings

RESET_REQUEST_URL = "/_allauth/app/v1/auth/password/request"


def test_email_limits_add_an_hourly_cap_per_address() -> None:
    limits = account_settings.RATE_LIMITS

    # allauth's documented format: amount/period/ip, user or key, comma
    # separated. The next test shows allauth enforcing a "/h/key" cap.
    assert {
        action: limits[action] for action in ("reset_password", "confirm_email")
    } == {
        "reset_password": "20/m/ip,5/m/key,20/h/key",
        "confirm_email": "1/10s/key,10/h/key",
    }
    # The template does not override signup (allauth's default remains).
    assert "signup" not in django_settings.ACCOUNT_RATE_LIMITS


@pytest.mark.django_db
def test_hourly_cap_stops_reset_emails_to_one_address(
    settings: Settings, client: Client
) -> None:
    # A new dict, not an in-place change: allauth reads the setting per request.
    settings.ACCOUNT_RATE_LIMITS = {"reset_password": "100/m/ip,100/m/key,2/h/key"}
    user = UserFactory()

    responses = [
        client.post(
            RESET_REQUEST_URL, {"email": user.email}, content_type="application/json"
        )
        for _ in range(3)
    ]

    assert [r.status_code for r in responses] == [
        HTTPStatus.UNAUTHORIZED,  # pending password_reset_by_code flow
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.TOO_MANY_REQUESTS,
    ]
    assert len(mail.outbox) == 2  # noqa: PLR2004
