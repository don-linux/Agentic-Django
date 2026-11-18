"""Each account email uses the project's subject and wording, not allauth's."""

from __future__ import annotations

import re
from http import HTTPStatus

import pytest
from allauth.account.models import EmailAddress
from django.conf import settings
from django.core import mail
from django.test import Client

from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

PASSWORD = "My_R@ndom-P@ssw0rd"  # noqa: S105
SIGNUP_URL = "/_allauth/app/v1/auth/signup"
LOGIN_URL = "/_allauth/app/v1/auth/login"
VERIFY_URL = "/_allauth/app/v1/auth/email/verify"
RESEND_URL = "/_allauth/app/v1/auth/email/verify/resend"
RESET_REQUEST_URL = "/_allauth/app/v1/auth/password/request"
MANAGE_EMAIL_URL = "/_allauth/app/v1/account/email"
REAUTH_URL = "/_allauth/app/v1/auth/reauthenticate"

STOCK_PHRASES = (
    "password forgotten procedure",
    "in our database",
    "browser window",
    "Please Confirm Your Email Address",
    "Account Already Exists",
    "Unknown Account",
    "Hello from",
)


@pytest.fixture(autouse=True)
def no_confirm_email_limit(settings) -> None:
    settings.ACCOUNT_RATE_LIMITS = {
        **getattr(settings, "ACCOUNT_RATE_LIMITS", {}),
        "confirm_email": None,
        "reset_password": None,
    }


def _subject(rest: str) -> str:
    return settings.ACCOUNT_EMAIL_SUBJECT_PREFIX + rest


def _assert_headless_body(message: mail.EmailMessage) -> None:
    body = str(message.body)
    assert settings.FRONTEND_URL in body
    assert settings.EMAIL_SIGNATURE_NAME in body
    assert "\n-- \n" in body
    assert "browser window" not in body
    assert "testserver" not in body
    assert getattr(message, "alternatives", []) == []
    assert message.content_subtype == "plain"
    assert message.from_email == settings.DEFAULT_FROM_EMAIL
    for phrase in ("password forgotten procedure", "in our database", "Hello from"):
        assert phrase not in body
    assert "Please Confirm Your Email Address" not in str(message.subject)


def _code(message: mail.EmailMessage | None = None) -> str:
    body = str((message or mail.outbox[-1]).body)
    match = re.search(r"\b([A-Z0-9]{4}-[A-Z0-9]{4})\b", body)
    assert match, body
    return match.group(1)


def _signup(client: Client, email: str) -> str:
    response = client.post(
        SIGNUP_URL,
        {"email": email, "password": PASSWORD},
        content_type="application/json",
    )
    assert response.status_code == HTTPStatus.UNAUTHORIZED, response.content
    return response.json()["meta"]["session_token"]


def _signup_and_verify(email: str) -> tuple[Client, str]:
    client = Client()
    token = _signup(client, email)
    verified = client.post(
        VERIFY_URL,
        {"key": _code()},
        content_type="application/json",
        HTTP_X_SESSION_TOKEN=token,
    )
    assert verified.status_code == HTTPStatus.OK, verified.content
    return Client(), verified.json()["meta"]["session_token"]


def test_signup_uses_the_project_verification_subject_and_signup_copy(
    client: Client,
) -> None:
    _signup(client, "tpl-signup@example.com")

    message = mail.outbox[-1]
    _assert_headless_body(message)
    assert str(message.subject) == _subject("Your verification code")
    assert "create an account" in str(message.body)
    assert "enter this code in the app" in str(message.body)
    assert "added this email address" not in str(message.body)
    assert _code(message)


def test_resend_keeps_the_signup_copy(client: Client) -> None:
    token = _signup(client, "tpl-resend@example.com")
    mail.outbox.clear()

    response = client.post(RESEND_URL, HTTP_X_SESSION_TOKEN=token)

    assert response.status_code == HTTPStatus.OK
    message = mail.outbox[-1]
    _assert_headless_body(message)
    assert str(message.subject) == _subject("Your verification code")
    assert "create an account" in str(message.body)


def test_login_while_unverified_uses_the_signup_copy() -> None:
    user = UserFactory(email="tpl-pending@example.com", password=PASSWORD)
    EmailAddress.objects.create(
        user=user, email=user.email, primary=True, verified=False
    )

    response = Client().post(
        LOGIN_URL,
        {"email": user.email, "password": PASSWORD},
        content_type="application/json",
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    message = mail.outbox[-1]
    _assert_headless_body(message)
    assert str(message.subject) == _subject("Your verification code")
    assert "create an account" in str(message.body)
    assert "added this email address" not in str(message.body)


def test_add_email_uses_distinct_copy_not_the_signup_wording() -> None:
    _client, session = _signup_and_verify("tpl-adder@example.com")
    reauthenticated = _client.post(
        REAUTH_URL,
        {"password": PASSWORD},
        content_type="application/json",
        HTTP_X_SESSION_TOKEN=session,
    )
    assert reauthenticated.status_code == HTTPStatus.OK, reauthenticated.content
    mail.outbox.clear()

    response = _client.post(
        MANAGE_EMAIL_URL,
        {"email": "tpl-second@example.com"},
        content_type="application/json",
        HTTP_X_SESSION_TOKEN=session,
    )

    assert response.status_code == HTTPStatus.OK, response.content
    message = mail.outbox[-1]
    _assert_headless_body(message)
    assert str(message.subject) == _subject("Your verification code")
    assert "added this email address" in str(message.body)
    assert "create an account" not in str(message.body)
    assert "enter this code in the app" in str(message.body)


def test_password_reset_of_a_known_address_uses_the_project_code_subject(
    client: Client,
) -> None:
    user = UserFactory(email="tpl-reset@example.com", password=PASSWORD)
    EmailAddress.objects.create(
        user=user, email=user.email, primary=True, verified=True
    )

    response = client.post(
        RESET_REQUEST_URL, {"email": user.email}, content_type="application/json"
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    message = mail.outbox[-1]
    _assert_headless_body(message)
    assert str(message.subject) == _subject("Your password reset code")
    assert "reset your password" in str(message.body)
    assert "Password Reset Code" not in str(message.subject)


def test_already_exists_email_uses_the_project_copy(
    client: Client,
) -> None:
    existing = UserFactory(email="tpl-existing@example.com", password=PASSWORD)
    EmailAddress.objects.create(
        user=existing, email=existing.email, primary=True, verified=True
    )

    response = client.post(
        SIGNUP_URL,
        {"email": "Tpl-Existing@Example.com", "password": "Another_Str0ng-P@ss!"},
        content_type="application/json",
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    message = mail.outbox[-1]
    _assert_headless_body(message)
    assert str(message.subject) == _subject("Account already exists")
    assert "An account already exists" in str(message.body)
    assert f"{settings.FRONTEND_URL}/account/password/reset" in str(message.body)
    for phrase in STOCK_PHRASES:
        assert phrase not in str(message.subject)
        assert phrase not in str(message.body)


def test_unknown_account_email_uses_the_project_copy(
    client: Client,
) -> None:
    response = client.post(
        RESET_REQUEST_URL,
        {"email": "tpl-nobody@example.com"},
        content_type="application/json",
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    message = mail.outbox[-1]
    _assert_headless_body(message)
    assert str(message.subject) == _subject("No account for this email")
    assert "No account uses it" in str(message.body)
    assert f"{settings.FRONTEND_URL}/account/signup" in str(message.body)
    for phrase in STOCK_PHRASES:
        assert phrase not in str(message.subject)
        assert phrase not in str(message.body)


def test_custom_host_does_not_appear_in_enumeration_or_code_emails(settings) -> None:
    host = "api.example.test"
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, host]
    client = Client()
    _signup(client, "tpl-host@example.com")
    existing = UserFactory(email="tpl-host-dup@example.com", password=PASSWORD)
    EmailAddress.objects.create(
        user=existing, email=existing.email, primary=True, verified=True
    )
    client.post(
        SIGNUP_URL,
        {"email": existing.email, "password": "Another_Str0ng-P@ss!"},
        content_type="application/json",
        HTTP_HOST=host,
    )
    client.post(
        RESET_REQUEST_URL,
        {"email": "tpl-host-unknown@example.com"},
        content_type="application/json",
        HTTP_HOST=host,
    )

    assert mail.outbox
    for message in mail.outbox:
        assert host not in str(message.subject)
        assert host not in str(message.body)
        assert "testserver" not in str(message.body)
