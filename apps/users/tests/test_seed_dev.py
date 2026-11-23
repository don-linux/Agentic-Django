from __future__ import annotations

from http import HTTPStatus

import pytest
from allauth.account.models import EmailAddress
from django.core.management import CommandError
from django.core.management import call_command
from django.test import Client
from django.test import override_settings

from apps.users.models import User

pytestmark = pytest.mark.django_db

PASSWORD = "seed-test-password"  # noqa: S105


def _seed(*args: str) -> None:
    call_command("seed_dev", "--password", PASSWORD, *args)


@override_settings(DEBUG=True)
def test_seed_dev_is_idempotent() -> None:
    _seed()
    _seed()

    assert (
        User.objects.filter(email="admin@example.com", is_superuser=True).count() == 1
    )
    assert (
        User.objects.filter(email="user@example.com", is_superuser=False).count() == 1
    )
    assert EmailAddress.objects.filter(verified=True, primary=True).count() == 2  # noqa: PLR2004


@override_settings(DEBUG=True)
def test_seeding_again_keeps_the_seeded_users_signed_in() -> None:
    # docs/development.md: running it again changes nothing, so a session
    # token from the first run still works after the second.
    _seed()
    token = (
        Client()
        .post(
            "/_allauth/app/v1/auth/login",
            {"email": "user@example.com", "password": PASSWORD},
            content_type="application/json",
        )
        .json()["meta"]["session_token"]
    )
    _seed()
    me = Client().get("/api/v1/users/me/", HTTP_X_SESSION_TOKEN=token)
    assert me.status_code == HTTPStatus.OK


@override_settings(DEBUG=True)
def test_seeding_reactivates_a_deactivated_seed_user() -> None:
    _seed()
    User.objects.filter(email="user@example.com").update(is_active=False)
    _seed()
    assert User.objects.get(email="user@example.com").is_active is True


@override_settings(DEBUG=True)
def test_seeded_user_can_log_in_through_allauth_headless() -> None:
    _seed()

    response = Client().post(
        "/_allauth/app/v1/auth/login",
        {"email": "user@example.com", "password": PASSWORD},
        content_type="application/json",
    )

    assert response.status_code == HTTPStatus.OK
    token = response.json()["meta"]["session_token"]
    me = Client().get("/api/v1/users/me/", HTTP_X_SESSION_TOKEN=token)
    assert me.json()["name"] == "Dev User"


def test_seed_dev_refuses_to_run_without_debug() -> None:
    with pytest.raises(CommandError, match="DEBUG"):
        _seed()


def test_seed_dev_runs_without_debug_when_explicitly_allowed() -> None:
    _seed("--allow-non-debug")

    assert User.objects.filter(email="user@example.com").exists()


@override_settings(DEBUG=True)
def test_seed_dev_runs_app_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "apps.users.management.commands.seed_dev.importlib.util.find_spec",
        lambda name: object() if name == "apps.users.seed" else None,
    )
    monkeypatch.setattr(
        "apps.users.management.commands.seed_dev.importlib.import_module",
        lambda name: type(
            "Seed", (), {"seed_dev": staticmethod(lambda: calls.append(name))}
        ),
    )

    _seed()

    assert calls == ["apps.users.seed"]


@override_settings(DEBUG=True)
def test_seed_dev_restores_the_seeded_flags() -> None:
    call_command("seed_dev", verbosity=0)
    admin = User.objects.get(email="admin@example.com")
    User.objects.filter(pk=admin.pk).update(
        is_active=False, is_staff=False, is_superuser=False, name="Tampered"
    )

    call_command("seed_dev", verbosity=0)

    admin.refresh_from_db()
    assert (admin.is_active, admin.is_staff, admin.is_superuser) == (True, True, True)
    assert admin.name == "Dev Admin"


@override_settings(DEBUG=True)
def test_seed_dev_finds_a_mixed_case_email() -> None:
    call_command("seed_dev", verbosity=0)
    # A row stored before emails were normalized; update() skips save().
    User.objects.filter(email="user@example.com").update(email="User@Example.com")

    call_command("seed_dev", verbosity=0)

    users = list(User.objects.filter(email__iexact="user@example.com"))
    assert len(users) == 1
    assert users[0].email == "user@example.com"


@override_settings(DEBUG=True)
def test_seed_dev_verifies_and_promotes_the_seeded_address() -> None:
    user = User.objects.create_user("user@example.com", PASSWORD)
    EmailAddress.objects.create(
        user=user, email="old@example.com", primary=True, verified=True
    )
    EmailAddress.objects.create(
        user=user, email=user.email, primary=False, verified=False
    )

    _seed()

    # The same rule as the admin's email change (user_primary_email_sync).
    assert list(
        EmailAddress.objects.filter(user=user).values_list(
            "email", "primary", "verified"
        )
    ) == [("user@example.com", True, True)]


def test_seed_dev_lives_in_the_users_app() -> None:
    from django.core.management import get_commands  # noqa: PLC0415

    assert get_commands()["seed_dev"] == "apps.users"
