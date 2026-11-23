from __future__ import annotations

import importlib
import re
from http import HTTPStatus

import pytest
from allauth.account.models import EmailAddress
from django.apps import apps
from django.core import mail
from django.test import Client
from django.test import override_settings
from django.urls import NoReverseMatch
from django.urls import clear_url_caches
from django.urls import reverse

import config.urls
from apps.users.models import User
from apps.users.tests.factories import UserFactory
from core.tests.credentials import PASSWORD

pytestmark = pytest.mark.django_db

SIGNUP_URL = "/_allauth/app/v1/auth/signup"
LOGIN_URL = "/_allauth/app/v1/auth/login"
VERIFY_URL = "/_allauth/app/v1/auth/email/verify"
RESET_REQUEST_URL = "/_allauth/app/v1/auth/password/request"
RESET_CONFIRM_URL = "/_allauth/app/v1/auth/password/reset"
ORIGIN = "http://localhost:3000"


def _verification_code() -> str:
    body = str(mail.outbox[-1].body)
    match = re.search(r"(?m)^([A-Z0-9]+(?:-[A-Z0-9]+)*)$", body)
    assert match is not None, body
    return match.group(1)


def _has_pending_flow(payload: dict, flow_id: str) -> bool:
    flows = payload["data"]["flows"]
    return any(flow["id"] == flow_id and flow.get("is_pending") for flow in flows)


def _pending_verify_email(payload: dict) -> bool:
    return _has_pending_flow(payload, "verify_email")


def test_root_returns_not_found_when_debug_is_false(client) -> None:
    response = client.get("/")

    assert response.status_code == HTTPStatus.NOT_FOUND


def test_root_redirects_to_admin_when_debug_is_true(client) -> None:
    try:
        with override_settings(DEBUG=True):
            importlib.reload(config.urls)
            clear_url_caches()
            response = client.get("/")
    finally:
        importlib.reload(config.urls)
        clear_url_caches()

    assert response.status_code == HTTPStatus.FOUND
    assert response.headers["Location"] == "/admin/"


def test_admin_login_is_django(client) -> None:
    response = client.get("/admin/login/")

    assert response.status_code == HTTPStatus.OK
    assert any(template.name == "admin/login.html" for template in response.templates)


def test_html_login_and_signup_are_gone(client) -> None:
    with pytest.raises(NoReverseMatch):
        reverse("account_login")
    with pytest.raises(NoReverseMatch):
        reverse("account_signup")

    assert client.get("/accounts/login/").status_code == HTTPStatus.NOT_FOUND
    assert client.get("/accounts/signup/").status_code == HTTPStatus.NOT_FOUND


def test_signup_returns_json_and_sends_code(client: Client) -> None:
    response = client.post(
        SIGNUP_URL,
        {"email": "new-user@example.com", "password": PASSWORD},
        content_type="application/json",
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.headers["Content-Type"].startswith("application/json")
    payload = response.json()
    assert payload["meta"]["is_authenticated"] is False
    assert payload["meta"]["session_token"]
    assert _pending_verify_email(payload)
    assert User.objects.filter(email="new-user@example.com").exists()
    assert len(mail.outbox) == 1
    assert _verification_code()


def test_signup_respects_closed_registration(settings, client: Client) -> None:
    settings.ACCOUNT_ALLOW_REGISTRATION = False

    response = client.post(
        SIGNUP_URL,
        {"email": "closed@example.com", "password": PASSWORD},
        content_type="application/json",
    )

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert not User.objects.filter(email="closed@example.com").exists()


def test_unverified_login_is_not_authenticated(client: Client) -> None:
    password = PASSWORD
    user = UserFactory(email="pending@example.com", password=password)
    EmailAddress.objects.create(
        user=user,
        email=user.email,
        primary=True,
        verified=False,
    )
    login = client.post(
        LOGIN_URL,
        {"email": "pending@example.com", "password": password},
        content_type="application/json",
    )

    assert login.status_code == HTTPStatus.UNAUTHORIZED
    payload = login.json()
    assert payload["meta"]["is_authenticated"] is False
    assert _pending_verify_email(payload)


def test_verify_code_grants_api_access() -> None:
    client = Client()
    signup = client.post(
        SIGNUP_URL,
        {"email": "verified@example.com", "password": PASSWORD},
        content_type="application/json",
    )
    token = signup.json()["meta"]["session_token"]

    verified = client.post(
        VERIFY_URL,
        {"key": _verification_code()},
        content_type="application/json",
        HTTP_X_SESSION_TOKEN=token,
    )

    assert verified.status_code == HTTPStatus.OK
    assert verified.json()["meta"]["is_authenticated"] is True
    session_token = verified.json()["meta"]["session_token"]

    api = Client()
    me = api.get("/api/v1/users/me/", HTTP_X_SESSION_TOKEN=session_token)

    assert me.status_code == HTTPStatus.OK
    assert me.json()["name"] == ""


def test_pending_signup_token_does_not_authenticate_api_requests() -> None:
    client = Client()
    signup = client.post(
        SIGNUP_URL,
        {"email": "unverified-token@example.com", "password": PASSWORD},
        content_type="application/json",
    )
    pending_token = signup.json()["meta"]["session_token"]

    me = Client().get("/api/v1/users/me/", HTTP_X_SESSION_TOKEN=pending_token)

    assert me.status_code == HTTPStatus.UNAUTHORIZED


def test_duplicate_email_signup_prevents_enumeration_and_sends_reset_link(
    settings,
    client: Client,
) -> None:
    existing = UserFactory(email="existing@example.com")
    EmailAddress.objects.create(
        user=existing,
        email=existing.email,
        primary=True,
        verified=True,
    )

    response = client.post(
        SIGNUP_URL,
        {"email": "Existing@Example.com", "password": "Another_Str0ng-P@ss!"},
        content_type="application/json",
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    payload = response.json()
    assert payload["meta"]["is_authenticated"] is False
    assert _pending_verify_email(payload)
    assert User.objects.filter(email="existing@example.com").count() == 1
    assert len(mail.outbox) == 1
    assert f"{settings.FRONTEND_URL}/account/password/reset" in str(
        mail.outbox[-1].body,
    )


def test_password_reset_by_code_sets_the_password_the_user_logs_in_with() -> None:
    old_password = "Old_Str0ng-P@ssw0rd!"  # noqa: S105
    new_password = "New_Str0ng-P@ssw0rd!"  # noqa: S105
    user = UserFactory(email="reset-me@example.com", password=old_password)
    EmailAddress.objects.create(
        user=user,
        email=user.email,
        primary=True,
        verified=True,
    )

    client = Client()
    requested = client.post(
        RESET_REQUEST_URL,
        {"email": "Reset-Me@Example.com"},
        content_type="application/json",
    )

    assert requested.status_code == HTTPStatus.UNAUTHORIZED
    requested_payload = requested.json()
    assert _has_pending_flow(requested_payload, "password_reset_by_code")
    reset_session_token = requested_payload["meta"]["session_token"]
    assert len(mail.outbox) == 1
    code = _verification_code()

    completed = client.post(
        RESET_CONFIRM_URL,
        {"key": code, "password": new_password},
        content_type="application/json",
        HTTP_X_SESSION_TOKEN=reset_session_token,
    )

    # allauth's ACCOUNT_LOGIN_ON_PASSWORD_RESET is off: the reset itself does not
    # log in, so the client logs in with the new password next.
    assert completed.status_code == HTTPStatus.UNAUTHORIZED
    user.refresh_from_db()
    assert user.check_password(new_password)

    login = Client().post(
        LOGIN_URL,
        {"email": "reset-me@example.com", "password": new_password},
        content_type="application/json",
    )
    assert login.status_code == HTTPStatus.OK
    assert login.json()["meta"]["is_authenticated"] is True


def test_password_reset_for_unknown_email_prevents_enumeration_and_sends_signup_link(
    settings,
    client: Client,
) -> None:
    response = client.post(
        RESET_REQUEST_URL,
        {"email": "nobody-here@example.com"},
        content_type="application/json",
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert _has_pending_flow(response.json(), "password_reset_by_code")
    assert len(mail.outbox) == 1
    assert f"{settings.FRONTEND_URL}/account/signup" in str(mail.outbox[-1].body)


def test_mixed_case_signup_and_login() -> None:
    password = PASSWORD
    client = Client()
    signup = client.post(
        SIGNUP_URL,
        {"email": "Mixed.User@Example.COM", "password": password},
        content_type="application/json",
    )
    assert signup.status_code == HTTPStatus.UNAUTHORIZED
    assert User.objects.filter(email="mixed.user@example.com").exists()

    verified = client.post(
        VERIFY_URL,
        {"key": _verification_code()},
        content_type="application/json",
        HTTP_X_SESSION_TOKEN=signup.json()["meta"]["session_token"],
    )
    assert verified.status_code == HTTPStatus.OK

    login = Client().post(
        LOGIN_URL,
        {"email": "MIXED.user@example.com", "password": password},
        content_type="application/json",
    )
    assert login.status_code == HTTPStatus.OK
    assert login.json()["meta"]["is_authenticated"] is True


def test_allauth_preflight_allows_configured_origin(client, settings) -> None:
    settings.CORS_ALLOWED_ORIGINS = [ORIGIN]
    response = client.options(
        LOGIN_URL,
        HTTP_ORIGIN=ORIGIN,
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="x-session-token,content-type",
    )

    assert response.headers["Access-Control-Allow-Origin"] == ORIGIN
    allowed_headers = response.headers["Access-Control-Allow-Headers"].lower()
    assert "x-session-token" in allowed_headers


def test_api_v1_preflight_allows_configured_origin(client, settings) -> None:
    settings.CORS_ALLOWED_ORIGINS = [ORIGIN]
    response = client.options(
        "/api/v1/users/me/",
        HTTP_ORIGIN=ORIGIN,
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="x-session-token",
    )

    assert response.headers["Access-Control-Allow-Origin"] == ORIGIN
    allowed_headers = response.headers["Access-Control-Allow-Headers"].lower()
    assert "x-session-token" in allowed_headers


def test_allauth_preflight_rejects_unknown_origin(client) -> None:
    response = client.options(
        LOGIN_URL,
        HTTP_ORIGIN=ORIGIN,
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
    )

    assert "Access-Control-Allow-Origin" not in response.headers


def test_admin_preflight_has_no_cors(client, settings) -> None:
    settings.CORS_ALLOWED_ORIGINS = [ORIGIN]
    response = client.options(
        "/admin/login/",
        HTTP_ORIGIN=ORIGIN,
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
    )

    assert "Access-Control-Allow-Origin" not in response.headers


def test_login_and_session_identify_the_user_by_public_uuid(
    client: Client,
) -> None:
    user = UserFactory(email="uuid@example.com", password=PASSWORD)
    EmailAddress.objects.create(
        user=user, email=user.email, primary=True, verified=True
    )

    login = client.post(
        LOGIN_URL,
        {"email": user.email, "password": PASSWORD},
        content_type="application/json",
    )
    session = client.get(
        "/_allauth/app/v1/auth/session",
        HTTP_X_SESSION_TOKEN=login.json()["meta"]["session_token"],
    )

    for response in (login, session):
        assert response.status_code == HTTPStatus.OK
        payload_user = response.json()["data"]["user"]
        assert payload_user["id"] == str(user.public_id)
        assert payload_user["id"] != user.pk


def test_sites_framework_is_not_installed() -> None:
    # Headless API: the domain lives in ALLOWED_HOSTS and FRONTEND_URL, and
    # allauth falls back to the request host when sites is not installed.
    assert not apps.is_installed("django.contrib.sites")


@pytest.mark.parametrize("route", ["auth/login", "auth/signup"])
def test_allauth_json_nested_too_deeply_is_400(client, route: str) -> None:
    client.raise_request_exception = False
    body = "[" * 200_000 + "]" * 200_000

    response = client.post(
        f"/_allauth/app/v1/{route}", body, content_type="application/json"
    )

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.content == b""
