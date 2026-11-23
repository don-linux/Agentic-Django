"""Negative paths: bad tokens, permissions, outages, malformed payloads."""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from contextlib import contextmanager
from datetime import timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Any

import pytest
from allauth.account.models import EmailAddress
from axes.handlers.proxy import AxesProxyHandler
from django.contrib.messages import get_messages
from django.contrib.sessions.backends.base import UpdateError
from django.contrib.sessions.backends.cached_db import KEY_PREFIX as SESSION_PREFIX
from django.contrib.sessions.models import Session
from django.core.cache import caches
from django.core.mail.backends.locmem import EmailBackend
from django.core.management import call_command
from django.db import IntegrityError
from django.db import OperationalError
from django.template import TemplateSyntaxError
from django.test import Client
from django.test import RequestFactory
from django.test import override_settings
from django.urls import NoReverseMatch
from django.urls import Resolver404
from django.urls import resolve
from django.urls import reverse
from django.utils import timezone
from redis.exceptions import ConnectionError as ValkeyConnectionError

from apps.users.models import User
from apps.users.selectors import USER_NAMESPACE
from apps.users.services import user_deactivate
from apps.users.services import user_reactivate
from apps.users.tests.factories import UserFactory
from config import celery_app
from core.admin_lockout import is_outside_admin
from core.cache import build_key
from core.middleware import NO_REQUEST_ID
from core.middleware import RequestIdMiddleware
from core.middleware import request_id_var
from core.net import get_client_ip
from core.tasks import enqueue_on_commit
from core.tasks import ping
from core.tests.cache_helpers import DEAD_VALKEY
from core.tests.cache_helpers import SESSION_URL
from core.tests.cache_helpers import _login
from core.tests.cache_helpers import _root_cause
from core.tests.credentials import PASSWORD

if TYPE_CHECKING:
    from collections.abc import Generator

    from django.http import HttpRequest
    from django.http import HttpResponse
    from pytest_django.fixtures import DjangoCaptureOnCommitCallbacks
    from pytest_django.fixtures import Settings

    from conftest import CacheWrites
    from conftest import ThrottleRates
    from conftest import TrustedProxies

LOGIN_URL = "/_allauth/app/v1/auth/login"
SIGNUP_URL = "/_allauth/app/v1/auth/signup"
VERIFY_URL = "/_allauth/app/v1/auth/email/verify"
ME_URL = "/api/v1/users/me/"
# An outage answers fast: the connect timeouts bound it, not a hung socket.
OUTAGE_ANSWER_SECONDS = 3
# A broker outage must not hold the request that enqueues.
ENQUEUE_ANSWER_SECONDS = 2
CACHED_DB = "django.contrib.sessions.backends.cached_db"


@pytest.fixture
def valkey_outage(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    settings.CACHES = DEAD_VALKEY
    settings.SESSION_ENGINE = CACHED_DB
    monkeypatch.setattr(AxesProxyHandler, "implementation", None)


def _verified_user(email: str, **kwargs: Any) -> User:
    user = UserFactory(email=email, password=PASSWORD, **kwargs)
    EmailAddress.objects.create(
        user=user, email=user.email, primary=True, verified=True
    )
    return user


def _session_token(user: User, password: str = PASSWORD) -> str:
    response = Client().post(
        LOGIN_URL,
        {"email": user.email, "password": password},
        content_type="application/json",
    )
    assert response.status_code == HTTPStatus.OK, response.content
    return response.json()["meta"]["session_token"]


@contextmanager
def hanging_tcp_server() -> Generator[int]:
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    port = sock.getsockname()[1]
    stop = threading.Event()
    held: list[socket.socket] = []

    def serve() -> None:
        sock.settimeout(0.1)
        while not stop.is_set():
            try:
                conn, _ = sock.accept()
                held.append(conn)
            except TimeoutError:
                continue
        for conn in held:
            conn.close()
        sock.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        stop.set()
        thread.join(timeout=2)


@pytest.mark.django_db
@pytest.mark.parametrize("token", ["not-a-real-token", "", "deadbeef", "x" * 64])
def test_invalid_session_token_is_401_with_authenticate_header(
    client: Client, token: str
) -> None:
    response = client.get(ME_URL, HTTP_X_SESSION_TOKEN=token)

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.headers["WWW-Authenticate"] == "X-Session-Token"
    assert response.json() == {
        "code": "not_authenticated",
        "detail": "Unauthorized",
    }


@pytest.mark.django_db
def test_expired_session_token_is_401() -> None:
    user = _verified_user("expired-token@example.com")
    token = _session_token(user)
    Session.objects.filter(session_key=token).update(
        expire_date=timezone.now() - timedelta(days=1)
    )

    caches["default"].delete(f"{SESSION_PREFIX}{token}")

    response = Client().get(ME_URL, HTTP_X_SESSION_TOKEN=token)

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.headers["WWW-Authenticate"] == "X-Session-Token"
    assert response.json()["code"] == "not_authenticated"


def test_empty_header_falls_back_to_remote_addr(
    trusted_proxies: TrustedProxies,
) -> None:
    request = RequestFactory().get(
        "/", REMOTE_ADDR="172.18.0.1", HTTP_X_FORWARDED_FOR=""
    )
    with trusted_proxies(1):
        assert get_client_ip(request) == "172.18.0.1"


@override_settings(DEBUG=True)
@pytest.mark.django_db
def test_seed_dev_rolls_back_users_when_an_app_hook_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom() -> None:
        msg = "hook failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(
        "apps.users.management.commands.seed_dev.importlib.util.find_spec",
        lambda name: object() if name == "apps.users.seed" else None,
    )
    monkeypatch.setattr(
        "apps.users.management.commands.seed_dev.importlib.import_module",
        lambda name: type("Seed", (), {"seed_dev": staticmethod(boom)}),
    )

    with pytest.raises(RuntimeError, match="hook failed"):
        call_command("seed_dev", "--password", PASSWORD)

    assert not User.objects.filter(email="admin@example.com").exists()
    assert not User.objects.filter(email="user@example.com").exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("query", "field", "code"),
    [
        ("page=abc", "page", "int_parsing"),
        ("page=", "page", "int_parsing"),
        ("page=last", "page", "int_parsing"),
        ("page=0", "page", "greater_than_equal"),
        ("page=-1", "page", "greater_than_equal"),
        ("page_size=abc", "page_size", "int_parsing"),
        ("page_size=0", "page_size", "greater_than_equal"),
        ("page_size=-1", "page_size", "greater_than_equal"),
    ],
)
def test_users_list_rejects_a_malformed_page_or_page_size(
    user: User, user_client: Client, query: str, field: str, code: str
) -> None:
    response = user_client.get(f"/api/v1/users/?{query}")

    assert response.status_code == HTTPStatus.BAD_REQUEST
    body = response.json()
    assert body["code"] == "validation_error"
    assert [item["code"] for item in body["fields"][field]] == [code]


@pytest.mark.django_db
def test_users_list_cuts_a_page_size_above_100_and_404s_past_the_last_page(
    user: User, user_client: Client
) -> None:
    listed = user_client.get("/api/v1/users/?page_size=101")
    past_the_end = user_client.get("/api/v1/users/?page=2")

    assert listed.status_code == HTTPStatus.OK
    assert listed.json()["count"] == 1
    assert past_the_end.status_code == HTTPStatus.NOT_FOUND
    assert past_the_end.json() == {"code": "not_found", "detail": "Not Found"}


@pytest.mark.django_db
def test_health_cache_unavailable_on_oserror(
    client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_probe() -> None:
        msg = "Network is unreachable"
        raise OSError(msg)

    monkeypatch.setattr("core.middleware.probe", fail_probe)

    response = client.get("/health/")

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.content == b"cache unavailable\n"
    assert response.headers["Cache-Control"] == "no-store"
    assert b"unreachable" not in response.content
    assert b"Network" not in response.content


def test_resets_the_context_when_the_view_raises() -> None:
    def view(_request: HttpRequest) -> HttpResponse:
        msg = "view boom"
        raise RuntimeError(msg)

    request = RequestFactory().get("/")
    with pytest.raises(RuntimeError, match="view boom"):
        RequestIdMiddleware(view)(request)

    assert request_id_var.get() == NO_REQUEST_ID


def test_is_outside_admin_when_the_admin_is_not_routed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_admin(_name: str) -> str:
        msg = "admin:index"
        raise NoReverseMatch(msg)

    monkeypatch.setattr("core.admin_lockout.reverse", no_admin)
    request = RequestFactory().get("/_allauth/app/v1/auth/login")

    assert is_outside_admin(request, {}) is True


@pytest.mark.django_db
def test_staff_and_superuser_still_see_only_themselves() -> None:
    other = UserFactory()
    staff = UserFactory(is_staff=True, is_superuser=True)
    client = Client()
    client.force_login(staff)

    listed = client.get("/api/v1/users/")
    assert listed.status_code == HTTPStatus.OK
    assert listed.json()["count"] == 1
    assert listed.json()["results"][0]["id"] == str(staff.public_id)

    for method in ("get", "patch"):
        request = getattr(client, method)
        kwargs: dict[str, Any] = {}
        if method == "patch":
            kwargs = {"data": {"name": "Nope"}, "content_type": "application/json"}
        response = request(f"/api/v1/users/{other.public_id}/", **kwargs)
        assert response.status_code == HTTPStatus.NOT_FOUND
        assert response.json()["code"] == "not_found"


@pytest.mark.django_db
def test_anonymous_get_of_user_detail_is_401(user: User, client: Client) -> None:
    response = client.get(f"/api/v1/users/{user.public_id}/")

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.headers["WWW-Authenticate"] == "X-Session-Token"
    assert response.json()["code"] == "not_authenticated"


@pytest.mark.django_db
def test_inactive_user_cannot_log_in_and_reactivate_restores_it() -> None:

    user = _verified_user("deactivated-login@example.com")
    user_deactivate(user=user)

    denied = Client().post(
        LOGIN_URL,
        {"email": user.email, "password": PASSWORD},
        content_type="application/json",
    )
    assert denied.status_code == HTTPStatus.UNAUTHORIZED
    assert denied.json()["meta"]["is_authenticated"] is False

    user_reactivate(user=user)
    allowed = Client().post(
        LOGIN_URL,
        {"email": user.email, "password": PASSWORD},
        content_type="application/json",
    )
    assert allowed.status_code == HTTPStatus.OK
    assert allowed.json()["meta"]["is_authenticated"] is True


@pytest.mark.django_db
@pytest.mark.parametrize(
    "payload",
    [{"name": ["Ada"]}, {"name": None}, {"name": True}, {"name": {"obj": 1}}],
)
@pytest.mark.parametrize("target", ["me", "detail"])
def test_patch_rejects_an_invalid_name(
    user: User,
    user_client: Client,
    payload: dict[str, Any],
    target: str,
) -> None:
    original = user.name
    path = ME_URL if target == "me" else f"/api/v1/users/{user.public_id}/"
    response = user_client.patch(path, payload, content_type="application/json")

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["code"] == "validation_error"
    assert response.json()["fields"]["name"][0] == {
        "code": "string_type",
        "detail": "Input should be a valid string",
    }
    user.refresh_from_db()
    assert user.name == original


@pytest.mark.django_db
@pytest.mark.parametrize("payload", [[], "just a string"])
def test_patch_me_rejects_a_non_object_body(
    user: User, user_client: Client, payload: Any
) -> None:
    # django.test.Client sends a str body as is: encode it so the API gets JSON.
    response = user_client.patch(
        ME_URL, json.dumps(payload), content_type="application/json"
    )

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["code"] == "validation_error"
    assert "non_field_errors" in response.json()["fields"]


@pytest.mark.django_db
@pytest.mark.parametrize("number", [123, 1.5])
def test_a_number_for_name_is_rejected(
    user: User, user_client: Client, number: float
) -> None:
    original = user.name
    response = user_client.patch(
        ME_URL, {"name": number}, content_type="application/json"
    )

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["fields"]["name"][0]["code"] == "string_type"
    user.refresh_from_db()
    assert user.name == original


@pytest.mark.django_db
def test_name_is_stored_as_sent_and_its_length_counts_spaces(
    user: User, user_client: Client
) -> None:
    padded = user_client.patch(
        ME_URL, {"name": "x" * 255 + " " * 10}, content_type="application/json"
    )
    spaced = user_client.patch(
        ME_URL, {"name": "  Ada  "}, content_type="application/json"
    )

    # maxLength is 255 in the contract: 265 characters are too many, even when
    # the last ten are spaces. A name that fits is kept as sent, not trimmed.
    assert padded.status_code == HTTPStatus.BAD_REQUEST
    assert padded.json()["fields"]["name"][0]["code"] == "string_too_long"
    assert spaced.status_code == HTTPStatus.OK
    assert spaced.json()["name"] == "  Ada  "
    user.refresh_from_db()
    assert user.name == "  Ada  "


# DELETE on the detail path: test_error_format.test_unsupported_method_is_405.


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("method", "path_key", "allow"),
    [
        ("post", "list", "GET, HEAD, OPTIONS"),
        ("post", "me", "GET, PATCH, HEAD, OPTIONS"),
        ("put", "me", "GET, PATCH, HEAD, OPTIONS"),
        ("delete", "me", "GET, PATCH, HEAD, OPTIONS"),
        ("delete", "detail", "GET, PATCH, HEAD, OPTIONS"),
    ],
)
def test_disallowed_methods_are_rejected(
    user: User,
    user_client: Client,
    method: str,
    path_key: str,
    allow: str,
) -> None:
    paths = {
        "list": "/api/v1/users/",
        "me": ME_URL,
        "detail": f"/api/v1/users/{user.public_id}/",
    }
    kwargs: dict[str, Any] = {}
    if method in {"post", "put"}:
        kwargs = {"data": {"name": "Nope"}, "content_type": "application/json"}
    response = getattr(user_client, method)(paths[path_key], **kwargs)

    assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert response.headers["Allow"] == allow
    assert response.json()["code"] == "method_not_allowed"


@pytest.mark.django_db
def test_signup_survives_an_oserror_from_the_mailer(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    client: Client,
) -> None:
    def fail(self: EmailBackend, messages: object) -> int:
        msg = "Network is unreachable"
        raise OSError(msg)

    monkeypatch.setattr(EmailBackend, "send_messages", fail)

    with caplog.at_level(logging.ERROR, logger="apps.users.adapters"):
        response = client.post(
            SIGNUP_URL,
            {"email": "oserror@example.com", "password": PASSWORD},
            content_type="application/json",
        )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    payload = response.json()
    assert any(
        flow["id"] == "verify_email" and flow.get("is_pending")
        for flow in payload["data"]["flows"]
    )
    assert User.objects.filter(email="oserror@example.com").exists()
    assert "Could not send" in caplog.text
    assert "OSError" in caplog.text or "unreachable" in caplog.text


@pytest.mark.django_db
def test_a_broken_email_template_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
    client: Client,
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        msg = "broken template"
        raise TemplateSyntaxError(msg)

    monkeypatch.setattr(
        "allauth.account.adapter.DefaultAccountAdapter.render_mail", boom
    )

    with pytest.raises(TemplateSyntaxError, match="broken template"):
        client.post(
            SIGNUP_URL,
            {"email": "broken-template@example.com", "password": PASSWORD},
            content_type="application/json",
        )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("password", "code"),
    [("short", "password_too_short"), ("password", "password_too_common")],
)
def test_signup_rejects_a_short_or_common_password(
    client: Client, password: str, code: str
) -> None:
    response = client.post(
        SIGNUP_URL,
        {"email": "weak@example.com", "password": password},
        content_type="application/json",
    )

    assert response.status_code == HTTPStatus.BAD_REQUEST
    codes = [error["code"] for error in response.json()["errors"]]
    assert code in codes
    assert "session_token" not in response.json().get("meta", {})
    assert not User.objects.filter(email="weak@example.com").exists()


@pytest.mark.django_db
def test_verify_rejects_an_incorrect_code(client: Client) -> None:
    signup = client.post(
        SIGNUP_URL,
        {"email": "bad-code@example.com", "password": PASSWORD},
        content_type="application/json",
    )
    token = signup.json()["meta"]["session_token"]

    response = Client().post(
        VERIFY_URL,
        {"key": "NOT-A-CODE"},
        content_type="application/json",
        HTTP_X_SESSION_TOKEN=token,
    )

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert any(error["code"] == "incorrect_code" for error in response.json()["errors"])


@pytest.mark.django_db
def test_login_rejects_missing_and_invalid_fields(
    client: Client,
) -> None:
    empty = client.post(LOGIN_URL, {}, content_type="application/json")
    assert empty.status_code == HTTPStatus.BAD_REQUEST
    codes = {(error.get("param"), error["code"]) for error in empty.json()["errors"]}
    assert ("email", "required") in codes
    assert ("password", "required") in codes

    invalid = client.post(
        LOGIN_URL,
        {"email": "not-an-email", "password": PASSWORD},
        content_type="application/json",
    )
    assert invalid.status_code == HTTPStatus.BAD_REQUEST
    assert any(error["code"] == "invalid" for error in invalid.json()["errors"])


@pytest.mark.django_db
def test_login_rejects_a_wrong_password_without_enumeration() -> None:
    user = _verified_user("known@example.com")
    wrong = Client().post(
        LOGIN_URL,
        {"email": user.email, "password": "Wrong-Password-1"},
        content_type="application/json",
    )
    unknown = Client().post(
        LOGIN_URL,
        {"email": "missing@example.com", "password": "Wrong-Password-1"},
        content_type="application/json",
    )

    assert wrong.status_code == unknown.status_code == HTTPStatus.BAD_REQUEST
    assert wrong.json()["errors"] == unknown.json()["errors"]
    assert all(
        error["code"] == "email_password_mismatch" for error in wrong.json()["errors"]
    )


@pytest.mark.django_db
def test_change_email_promotes_an_existing_non_primary_address(
    admin_client: Any,
) -> None:
    target = UserFactory(email="old-primary@example.com", name="Target")
    EmailAddress.objects.create(
        user=target, email="old-primary@example.com", primary=True, verified=True
    )
    EmailAddress.objects.create(
        user=target, email="secondary@example.com", primary=False, verified=False
    )
    url = reverse("admin:users_user_change", kwargs={"object_id": target.pk})
    response = admin_client.post(
        url,
        data={
            "email": "secondary@example.com",
            "name": "Target",
            "is_active": "on",
            "date_joined_0": "2026-01-01",
            "date_joined_1": "00:00:00",
            "initial-date_joined_0": "2026-01-01",
            "initial-date_joined_1": "00:00:00",
        },
    )

    assert response.status_code == HTTPStatus.FOUND
    target.refresh_from_db()
    assert target.email == "secondary@example.com"
    promoted = EmailAddress.objects.get(user=target, email="secondary@example.com")
    assert promoted.primary is True
    # An unverified secondary address stays unverified when promoted.
    assert promoted.verified is False
    assert not EmailAddress.objects.filter(
        user=target, email="old-primary@example.com"
    ).exists()


@pytest.mark.django_db
def test_deactivate_action_skips_self_but_deactivates_the_others(
    admin_client: Any, admin_user: User, user: User
) -> None:
    response = admin_client.post(
        reverse("admin:users_user_changelist"),
        {
            "action": "deactivate_users",
            "_selected_action": [admin_user.pk, user.pk],
        },
    )

    assert response.status_code == HTTPStatus.FOUND
    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("Skipped your own account" in message for message in messages)
    assert any("Deactivated 1 user" in message for message in messages)
    admin_user.refresh_from_db()
    user.refresh_from_db()
    assert admin_user.is_active is True
    assert user.is_active is False


@pytest.mark.django_db
def test_saving_last_login_with_another_field_still_invalidates(
    user: User,
    cache_writes: CacheWrites,
) -> None:
    before = build_key(USER_NAMESPACE, "probe")

    with cache_writes():
        user.last_login = timezone.now()
        user.name = "Also changed"
        user.save(update_fields=["last_login", "name"])

    assert build_key(USER_NAMESPACE, "probe") != before


@pytest.mark.django_db
def test_uppercase_public_id_is_routed_as_json(user: User, user_client: Client) -> None:
    # The lookup regex is case-insensitive, so DEBUG=True cannot serve
    # Django's HTML 404 for a hex-case typo. The row is found (UUID).
    upper = str(user.public_id).upper()
    resolve(f"/api/v1/users/{upper}/")

    response = user_client.get(f"/api/v1/users/{upper}/")
    assert response.status_code == HTTPStatus.OK
    assert response.headers["Content-Type"].startswith("application/json")
    assert response.json()["id"] == str(user.public_id)


@pytest.mark.django_db
def test_format_suffix_routes_are_not_registered(
    user: User, user_client: Client
) -> None:
    for path in (
        "/api/v1/users.json/",
        "/api/v1/users.json",
        "/api/v1/users/me.json/",
        f"/api/v1/users/{user.public_id}.json/",
    ):
        with pytest.raises(Resolver404):
            resolve(path)
        response = user_client.get(path)
        assert response.status_code == HTTPStatus.NOT_FOUND
        assert response.json()["code"] == "not_found"


@pytest.mark.django_db
@pytest.mark.usefixtures("valkey_outage")
def test_logout_during_outage_raises_update_error_in_the_test_client() -> None:
    token = _login()
    with pytest.raises(UpdateError) as excinfo:
        Client().delete(SESSION_URL, HTTP_X_SESSION_TOKEN=token)
    assert _root_cause(excinfo.value, ValkeyConnectionError)
    assert not Session.objects.exists()


@pytest.mark.django_db
@pytest.mark.usefixtures("valkey_outage")
def test_logout_during_a_valkey_outage_is_a_json_500_without_details() -> None:
    token = _login()
    response = Client(raise_request_exception=False).delete(
        SESSION_URL, HTTP_X_SESSION_TOKEN=token
    )

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert response.headers["Content-Type"] == "application/json"
    assert response.json() == {
        "code": "server_error",
        "detail": "Server Error",
    }
    assert b"127.0.0.1" not in response.content
    assert b"Connection" not in response.content
    assert not Session.objects.exists()


@pytest.mark.django_db
def test_readiness_is_503_quickly_when_valkey_hangs(
    client: Any, settings: Settings
) -> None:
    with hanging_tcp_server() as port:
        settings.CACHES = {
            "default": {
                "BACKEND": "core.cache_backend.ValkeyCache",
                "LOCATION": f"redis://127.0.0.1:{port}/7",
                "OPTIONS": {"socket_connect_timeout": 0.2, "socket_timeout": 0.2},
            },
        }
        started = time.monotonic()
        response = client.get("/health/")
        elapsed = time.monotonic() - started

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.content == b"cache unavailable\n"
    assert elapsed < OUTAGE_ANSWER_SECONDS


@pytest.mark.django_db
def test_the_api_is_served_quickly_when_valkey_hangs(
    user_client: Client, settings: Settings
) -> None:
    with hanging_tcp_server() as port:
        settings.CACHES = {
            "default": {
                "BACKEND": "core.cache_backend.ValkeyCache",
                "LOCATION": f"redis://127.0.0.1:{port}/7",
                "OPTIONS": {"socket_connect_timeout": 0.2, "socket_timeout": 0.2},
            },
        }
        started = time.monotonic()
        response = user_client.get(ME_URL)
        elapsed = time.monotonic() - started

    assert response.status_code == HTTPStatus.OK
    assert elapsed < 4 * 0.2 + 1.5


@pytest.mark.django_db
def test_patch_me_succeeds_during_a_valkey_outage(
    caplog: pytest.LogCaptureFixture,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
    request: pytest.FixtureRequest,
) -> None:
    user = _verified_user("patch-outage@example.com")
    client = Client()
    # force_login() saves a session: log in before the outage starts.
    client.force_login(user)
    request.getfixturevalue("valkey_outage")
    # on_commit hooks do not run inside the pytest-django transaction unless
    # we execute them: T5's log assertion is otherwise red on correct code.
    with (
        caplog.at_level(logging.ERROR),
        django_capture_on_commit_callbacks(execute=True),
    ):
        response = client.patch(
            ME_URL, {"name": "During Outage"}, content_type="application/json"
        )

    assert response.status_code == HTTPStatus.OK
    assert response.json()["name"] == "During Outage"
    user.refresh_from_db()
    assert user.name == "During Outage"
    joined = "\n".join(
        record.getMessage() + " " + (record.exc_text or "") for record in caplog.records
    )
    assert "on_commit" in joined or "_replace_version" in joined


@pytest.mark.django_db
def test_enqueue_on_commit_survives_a_closed_broker(
    caplog: pytest.LogCaptureFixture,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:

    saved = {
        "task_always_eager": celery_app.conf.task_always_eager,
        "broker_url": celery_app.conf.broker_url,
        "broker_transport_options": celery_app.conf.broker_transport_options,
        "task_publish_retry": celery_app.conf.task_publish_retry,
        "task_publish_retry_policy": celery_app.conf.task_publish_retry_policy,
    }
    try:
        celery_app.conf.task_always_eager = False
        celery_app.conf["broker_url"] = "redis://127.0.0.1:6390/7"
        celery_app.conf.broker_transport_options = {
            "socket_connect_timeout": 0.2,
            "socket_timeout": 0.2,
            "socket_keepalive": True,
        }
        celery_app.conf.task_publish_retry = False
        celery_app.conf.task_publish_retry_policy = {
            "max_retries": 0,
            "interval_max": 0,
        }
        celery_app.connection_for_write().release()

        started = time.monotonic()
        with (
            caplog.at_level(logging.ERROR),
            django_capture_on_commit_callbacks(execute=True),
        ):
            enqueue_on_commit(ping, email_hint="private-value")
        elapsed = time.monotonic() - started

        errors = [
            record for record in caplog.records if record.levelno == logging.ERROR
        ]
        assert [(record.name, record.getMessage()) for record in errors] == [
            ("core.tasks", "could not enqueue task core.ping"),
        ]
        assert "private-value" not in caplog.text
        assert elapsed < ENQUEUE_ANSWER_SECONDS
    finally:
        for name, value in saved.items():
            setattr(celery_app.conf, name, value)
        celery_app.connection_for_write().release()


@pytest.mark.django_db
def test_enqueue_on_commit_is_bounded_when_the_broker_hangs(
    caplog: pytest.LogCaptureFixture,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:

    saved = {
        "task_always_eager": celery_app.conf.task_always_eager,
        "broker_url": celery_app.conf.broker_url,
        "broker_transport_options": celery_app.conf.broker_transport_options,
        "task_publish_retry": celery_app.conf.task_publish_retry,
        "task_publish_retry_policy": celery_app.conf.task_publish_retry_policy,
    }
    try:
        with hanging_tcp_server() as port:
            celery_app.conf.task_always_eager = False
            celery_app.conf["broker_url"] = f"redis://127.0.0.1:{port}/7"
            celery_app.conf.broker_transport_options = {
                "socket_connect_timeout": 0.2,
                "socket_timeout": 0.2,
                "socket_keepalive": True,
            }
            celery_app.conf.task_publish_retry = False
            celery_app.conf.task_publish_retry_policy = {
                "max_retries": 0,
                "interval_max": 0,
            }
            started = time.monotonic()
            with (
                caplog.at_level(logging.ERROR),
                django_capture_on_commit_callbacks(execute=True),
            ):
                enqueue_on_commit(ping, email_hint="private-value")
            elapsed = time.monotonic() - started

        errors = [
            record for record in caplog.records if record.levelno == logging.ERROR
        ]
        assert [(record.name, record.getMessage()) for record in errors] == [
            ("core.tasks", "could not enqueue task core.ping"),
        ]
        assert "private-value" not in caplog.text
        assert elapsed < 4 * 0.2 + 1.5
    finally:
        for name, value in saved.items():
            setattr(celery_app.conf, name, value)
        celery_app.connection_for_write().release()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "exc",
    [
        OperationalError("could not connect to server at postgres_db"),
        IntegrityError("duplicate key value violates unique constraint"),
    ],
)
def test_unhandled_database_error_on_the_users_list_is_a_json_500(
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
) -> None:
    def fail(self: object, sql: object, params: object = None) -> None:
        raise exc

    client = Client(raise_request_exception=False)
    client.force_login(user)
    with monkeypatch.context() as patch:
        patch.setattr("django.db.backends.utils.CursorWrapper.execute", fail)
        response = client.get("/api/v1/users/")

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert response.headers["Content-Type"] == "application/json"
    assert response.json() == {
        "code": "server_error",
        "detail": "Server Error",
    }
    assert b"postgres_db" not in response.content
    assert b"duplicate key" not in response.content


@pytest.mark.django_db
def test_users_me_returns_429_with_retry_after(
    user: User, throttle_rates: ThrottleRates
) -> None:
    throttle_rates({"user": "2/min", "anon": "100/min"})
    client = Client()
    client.force_login(user)

    assert client.get(ME_URL).status_code == HTTPStatus.OK
    assert client.get(ME_URL).status_code == HTTPStatus.OK
    response = client.get(ME_URL)

    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert response.json()["code"] == "throttled"
    assert int(response.headers["Retry-After"]) > 0


@pytest.mark.django_db
def test_headless_login_is_rate_limited(settings: Settings) -> None:
    settings.ACCOUNT_RATE_LIMITS = {"login_failed": "1/m/ip"}
    user = _verified_user("rate-login@example.com")
    client = Client()
    first = client.post(
        LOGIN_URL,
        {"email": user.email, "password": "Wrong-Password-1"},
        content_type="application/json",
    )
    second = client.post(
        LOGIN_URL,
        {"email": user.email, "password": "Wrong-Password-1"},
        content_type="application/json",
    )

    # allauth's login_failed limiter is a 400 validation error, not HTTP 429.
    assert first.status_code == HTTPStatus.BAD_REQUEST
    assert first.json()["errors"][0]["code"] == "email_password_mismatch"
    assert second.status_code == HTTPStatus.BAD_REQUEST
    assert second.json()["errors"][0]["code"] == "too_many_login_attempts"


@pytest.mark.django_db
def test_headless_signup_is_rate_limited(settings: Settings) -> None:
    settings.ACCOUNT_RATE_LIMITS = {"signup": "1/m/ip"}
    client = Client()
    first = client.post(
        SIGNUP_URL,
        {"email": "rate1@example.com", "password": PASSWORD},
        content_type="application/json",
    )
    second = client.post(
        SIGNUP_URL,
        {"email": "rate2@example.com", "password": PASSWORD},
        content_type="application/json",
    )

    assert first.status_code == HTTPStatus.UNAUTHORIZED
    assert second.status_code == HTTPStatus.TOO_MANY_REQUESTS


@pytest.mark.django_db
def test_inactive_users_session_token_is_rejected() -> None:
    user = _verified_user("leftover-token@example.com")
    token = _session_token(user)
    user.is_active = False
    user.save(update_fields=["is_active"])

    response = Client().get(ME_URL, HTTP_X_SESSION_TOKEN=token)

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json() == {
        "code": "not_authenticated",
        "detail": "Unauthorized",
    }


@pytest.mark.django_db
def test_session_cookie_patch_without_csrf_is_json_403(user: User) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    response = client.patch(
        ME_URL, {"name": "No CSRF"}, content_type="application/json"
    )

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.headers["Content-Type"].startswith("application/json")
    assert response.json()["code"] == "permission_denied"
    assert "CSRF" in response.json()["detail"]
    user.refresh_from_db()
    assert user.name != "No CSRF"


@pytest.mark.django_db
def test_schema_and_docs_401_and_403_bodies(client: Any, user_client: Client) -> None:
    schema_anon = client.get(reverse("api-schema"))
    docs_anon = client.get(reverse("api-docs"))
    schema_user = user_client.get(reverse("api-schema"))
    docs_user = user_client.get(reverse("api-docs"))

    # The schema and docs views answer the API's JSON errors, not Ninja's
    # OpenAPI or HTML error pages.
    for response, status, token in (
        (schema_anon, HTTPStatus.UNAUTHORIZED, "not_authenticated"),
        (docs_anon, HTTPStatus.UNAUTHORIZED, "not_authenticated"),
        (schema_user, HTTPStatus.FORBIDDEN, "permission_denied"),
        (docs_user, HTTPStatus.FORBIDDEN, "permission_denied"),
    ):
        assert response.status_code == status
        assert response.headers["Content-Type"].startswith("application/json")
        assert token in response.content.decode()
