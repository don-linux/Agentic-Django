"""Account emails use the shared identity settings in every environment."""

from __future__ import annotations

import logging
import smtplib
from http import HTTPStatus
from typing import Any

import pytest
from allauth.account.models import EmailAddress
from django.conf import settings
from django.core import mail
from django.core.mail.backends.locmem import EmailBackend
from django.test import Client

from apps.users.models import User
from apps.users.tests.factories import UserFactory
from core.tests.production_settings import load_production_settings

pytestmark = pytest.mark.django_db

SIGNUP_URL = "/_allauth/app/v1/auth/signup"
RESET_REQUEST_URL = "/_allauth/app/v1/auth/password/request"
RESEND_URL = "/_allauth/app/v1/auth/email/verify/resend"
LOGIN_URL = "/_allauth/app/v1/auth/login"
PASSWORD = "My_R@ndom-P@ssw0rd"  # noqa: S105


def _assert_identity(message: mail.EmailMessage) -> None:
    assert message.from_email == settings.DEFAULT_FROM_EMAIL
    assert str(message.subject).startswith(settings.ACCOUNT_EMAIL_SUBJECT_PREFIX)


def test_identity_defaults_are_shared() -> None:
    assert settings.ACCOUNT_EMAIL_SUBJECT_PREFIX == settings.EMAIL_SUBJECT_PREFIX
    assert settings.SERVER_EMAIL == settings.DEFAULT_FROM_EMAIL


def test_signup_email_uses_the_shared_identity(client: Client) -> None:
    client.post(
        SIGNUP_URL,
        {"email": "new@example.com", "password": PASSWORD},
        content_type="application/json",
    )

    assert len(mail.outbox) == 1
    _assert_identity(mail.outbox[0])


def test_password_reset_email_uses_the_shared_identity(client: Client) -> None:
    user = UserFactory(email="reset@example.com", password=PASSWORD)
    EmailAddress.objects.create(
        user=user, email=user.email, primary=True, verified=True
    )

    client.post(
        RESET_REQUEST_URL, {"email": user.email}, content_type="application/json"
    )

    assert len(mail.outbox) == 1
    _assert_identity(mail.outbox[0])


def test_production_reads_the_mailer_timeout_from_env() -> None:
    default = load_production_settings("MAILERS")["MAILERS"]["default"]["OPTIONS"]
    custom = load_production_settings(
        "MAILERS",
        env={"DJANGO_MAILER_TIMEOUT": "3"},
    )["MAILERS"]["default"]["OPTIONS"]

    assert default["timeout"] == 10  # noqa: PLR2004
    assert custom["timeout"] == 3  # noqa: PLR2004


def _pending(payload: dict[str, object], flow_id: str) -> bool:
    flows = payload["data"]["flows"]  # type: ignore[index]  # pyright: ignore[reportIndexIssue]
    return any(flow["id"] == flow_id and flow.get("is_pending") for flow in flows)


@pytest.fixture
def smtp_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(self: EmailBackend, messages: object) -> int:
        msg = "Connection unexpectedly closed"
        raise smtplib.SMTPServerDisconnected(msg)

    monkeypatch.setattr(EmailBackend, "send_messages", fail)


@pytest.fixture
def no_confirm_email_limit(settings: Any) -> None:
    settings.ACCOUNT_RATE_LIMITS = {"confirm_email": None}


def _signup(client: Client, email: str = "smtp@example.com") -> dict:
    response = client.post(
        SIGNUP_URL,
        {"email": email, "password": PASSWORD},
        content_type="application/json",
    )
    assert response.status_code == HTTPStatus.UNAUTHORIZED
    return response.json()


def test_signup_survives_an_smtp_failure(
    smtp_down: None,
    caplog: pytest.LogCaptureFixture,
    client: Client,
) -> None:
    with caplog.at_level(logging.ERROR, logger="apps.users.adapters"):
        payload = _signup(client)

    assert _pending(payload, "verify_email")
    assert User.objects.filter(email="smtp@example.com").exists()
    assert "Could not send" in caplog.text
    assert "account/email/" in caplog.text


def test_password_reset_survives_an_smtp_failure(
    smtp_down: None,
    caplog: pytest.LogCaptureFixture,
    client: Client,
) -> None:
    user = UserFactory(email="reset-smtp@example.com", password=PASSWORD)
    EmailAddress.objects.create(
        user=user, email=user.email, primary=True, verified=True
    )

    with caplog.at_level(logging.ERROR, logger="apps.users.adapters"):
        response = client.post(
            RESET_REQUEST_URL, {"email": user.email}, content_type="application/json"
        )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert _pending(response.json(), "password_reset_by_code")
    assert "Could not send" in caplog.text


def test_an_smtp_failure_log_leaves_out_the_recipient(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    client: Client,
) -> None:
    def refuse(self: EmailBackend, messages: list[mail.EmailMessage]) -> int:
        raise smtplib.SMTPRecipientsRefused(
            dict.fromkeys(messages[0].to, (550, b"mailbox unavailable"))
        )

    monkeypatch.setattr(EmailBackend, "send_messages", refuse)

    with caplog.at_level(logging.ERROR, logger="apps.users.adapters"):
        _signup(client)

    assert "Could not send" in caplog.text
    assert "SMTPRecipientsRefused" in caplog.text
    assert "smtp@example.com" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_verification_code_can_be_resent(
    monkeypatch: pytest.MonkeyPatch,
    no_confirm_email_limit: None,
    client: Client,
) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(EmailBackend, "send_messages", lambda self, messages: 0)
        token = _signup(client)["meta"]["session_token"]

    response = client.post(RESEND_URL, HTTP_X_SESSION_TOKEN=token)

    assert response.status_code == HTTPStatus.OK
    assert len(mail.outbox) == 1


def test_resends_are_capped_and_login_starts_a_new_flow(
    no_confirm_email_limit: None,
) -> None:
    client = Client()
    token = _signup(client, "capped@example.com")["meta"]["session_token"]

    statuses = [
        client.post(RESEND_URL, HTTP_X_SESSION_TOKEN=token).status_code
        for _ in range(3)
    ]
    login = Client().post(
        LOGIN_URL,
        {"email": "capped@example.com", "password": PASSWORD},
        content_type="application/json",
    )

    assert statuses == [HTTPStatus.OK, HTTPStatus.OK, HTTPStatus.CONFLICT]
    assert login.status_code == HTTPStatus.UNAUTHORIZED
    assert _pending(login.json(), "verify_email")
    assert len(mail.outbox) == 4  # signup, two resends, and the new login flow  # noqa: PLR2004


def _assert_headless_body(message: mail.EmailMessage) -> None:
    body = str(message.body)
    assert settings.FRONTEND_URL in body
    assert settings.EMAIL_SIGNATURE_NAME in body
    assert "\n-- \n" in body
    assert "browser window" not in body
    assert "testserver" not in body
    assert getattr(message, "alternatives", []) == []


def test_signup_email_is_plain_text_signed_with_the_frontend(
    client: Client,
) -> None:
    _signup(client, "plain@example.com")

    message = mail.outbox[0]
    _assert_headless_body(message)
    assert "enter this code in the app" in str(message.body)


def test_password_reset_email_is_plain_text_signed_with_the_frontend(
    client: Client,
) -> None:
    user = UserFactory(email="plain-reset@example.com", password=PASSWORD)
    EmailAddress.objects.create(
        user=user, email=user.email, primary=True, verified=True
    )

    client.post(
        RESET_REQUEST_URL, {"email": user.email}, content_type="application/json"
    )

    message = mail.outbox[0]
    _assert_headless_body(message)
    assert "reset your password" in str(message.body)
