"""allauth's phone routes are mounted but phone support is off: none is a 500."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import pytest
from allauth.account.models import EmailAddress
from django.test import Client

from apps.users.adapters import AccountAdapter
from apps.users.tests.factories import UserFactory
from core.tests.credentials import PASSWORD

pytestmark = pytest.mark.django_db

BASE = "/_allauth/app/v1"
PHONE = "+525512345678"


def _token() -> str:
    user = UserFactory(email="phone@example.com", password=PASSWORD)
    EmailAddress.objects.create(
        user=user, email=user.email, primary=True, verified=True
    )
    response = Client().post(
        f"{BASE}/auth/login",
        {"email": user.email, "password": PASSWORD},
        content_type="application/json",
    )
    assert response.status_code == HTTPStatus.OK, response.content
    return response.json()["meta"]["session_token"]


def _call(method: str, route: str, token: str | None = None) -> Any:
    headers = {"HTTP_X_SESSION_TOKEN": token} if token else {}
    return getattr(Client(), method)(
        f"{BASE}{route}",
        {"phone": PHONE, "code": "123456"},
        content_type="application/json",
        **headers,
    )


def test_adding_a_phone_is_refused_with_400() -> None:
    response = _call("post", "/account/phone", _token())

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["errors"][0]["code"] == "phone_not_supported"


def test_a_user_has_no_phone() -> None:
    response = _call("get", "/account/phone", _token())

    assert response.status_code == HTTPStatus.OK
    assert response.json()["data"] == []


@pytest.mark.parametrize("authenticated", [False, True])
@pytest.mark.parametrize("route", ["/auth/phone/verify", "/auth/phone/verify/resend"])
def test_verification_without_a_pending_flow_is_409(
    route: str,
    *,
    authenticated: bool,
) -> None:
    response = _call("post", route, _token() if authenticated else None)

    assert response.status_code == HTTPStatus.CONFLICT


@pytest.mark.parametrize(
    ("method", "expected"),
    [("get", HTTPStatus.UNAUTHORIZED), ("post", HTTPStatus.CONFLICT)],
)
def test_anonymous_phone_management(method: str, expected: HTTPStatus) -> None:
    response = _call(method, "/account/phone")

    assert response.status_code == expected


def test_the_adapter_stores_and_finds_no_phone() -> None:
    adapter = AccountAdapter()
    user = UserFactory()

    assert adapter.get_phone(user) is None
    assert adapter.get_user_by_phone(PHONE) is None
    for call in (
        lambda: adapter.set_phone(user, PHONE, verified=True),
        lambda: adapter.set_phone_verified(user, PHONE),
        lambda: adapter.send_verification_code_sms(user, PHONE, "123456"),
    ):
        with pytest.raises(NotImplementedError, match=r"docs/auth/phone\.md"):
            call()


UNHANDLED_METHODS = ["put", "patch", "delete", "options", "head", "trace"]


@pytest.mark.parametrize("method", UNHANDLED_METHODS)
def test_anonymous_unhandled_phone_methods_are_401(method: str) -> None:
    response = _call(method, "/account/phone")

    assert response.status_code == HTTPStatus.UNAUTHORIZED


@pytest.mark.parametrize("method", UNHANDLED_METHODS)
def test_signed_in_unhandled_phone_methods_are_405(method: str) -> None:
    response = _call(method, "/account/phone", _token())

    assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert response["Allow"] == "GET, POST"
