from __future__ import annotations

import uuid
from http import HTTPStatus
from io import StringIO
from unittest import mock

import pytest
from allauth.account.models import EmailAddress
from django.contrib.admin.models import CHANGE
from django.contrib.admin.models import LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core.management import CommandError
from django.core.management import call_command
from django.db.models import ProtectedError
from django.db.models import RestrictedError
from django.test import Client

from apps.users.models import User
from apps.users.tests.factories import UserFactory
from core.tests.credentials import PASSWORD

pytestmark = pytest.mark.django_db

EMAIL = "gone-for-good@example.com"


def _user_with_history() -> User:
    user = UserFactory(email=EMAIL, password=PASSWORD)
    EmailAddress.objects.create(user=user, email=EMAIL, primary=True, verified=True)
    LogEntry.objects.create(
        user=user,
        content_type=ContentType.objects.get_for_model(User),
        object_id=str(user.pk),
        object_repr=EMAIL,
        action_flag=CHANGE,
    )
    return user


def _run(*args: str) -> str:
    out = StringIO()
    call_command("hard_delete_user", *args, stdout=out)
    return out.getvalue()


def test_noinput_with_the_email_deletes_the_user_and_its_cascade() -> None:
    user = _user_with_history()

    output = _run(str(user.public_id), "--noinput", "--confirm", EMAIL)

    assert not User.objects.filter(pk=user.pk).exists()
    assert not EmailAddress.objects.filter(email=EMAIL).exists()
    assert not LogEntry.objects.exists()
    assert "users.User: 1" in output
    assert "account.EmailAddress: 1" in output
    assert "admin.LogEntry: 1" in output


def test_the_admin_history_other_admins_wrote_loses_the_email() -> None:
    user = _user_with_history()
    staff = UserFactory(is_staff=True)
    for target in (user, EmailAddress.objects.get(user=user)):
        LogEntry.objects.create(
            user=staff,
            content_type=ContentType.objects.get_for_model(target),
            object_id=str(target.pk),
            object_repr=str(target),
            action_flag=CHANGE,
        )

    _run(str(user.public_id), "--noinput", "--confirm", EMAIL)

    assert (
        list(LogEntry.objects.values_list("object_repr", flat=True))
        == [
            f"deleted-{user.public_id}@anonymized.invalid",
        ]
        * 2
    )


@pytest.mark.parametrize("confirm", ["", "someone@example.com"])
def test_noinput_without_a_matching_email_deletes_nothing(confirm: str) -> None:
    user = _user_with_history()

    with pytest.raises(CommandError, match="nothing deleted"):
        _run(str(user.public_id), "--noinput", "--confirm", confirm)

    assert User.objects.filter(pk=user.pk).exists()


@pytest.mark.parametrize(("typed", "deleted"), [(EMAIL, True), ("no", False)])
def test_interactive_confirmation(typed: str, *, deleted: bool) -> None:
    user = _user_with_history()

    with mock.patch("builtins.input", return_value=typed):
        if deleted:
            _run(str(user.public_id))
        else:
            with pytest.raises(CommandError):
                _run(str(user.public_id))

    assert User.objects.filter(pk=user.pk).exists() is not deleted


def test_an_unknown_public_id_is_an_error() -> None:
    with pytest.raises(CommandError, match="No user"):
        _run(str(uuid.uuid7()), "--noinput", "--confirm", EMAIL)


def test_the_user_is_signed_out_before_the_row_goes() -> None:
    user = _user_with_history()
    login = Client().post(
        "/_allauth/app/v1/auth/login",
        {"email": EMAIL, "password": PASSWORD},
        content_type="application/json",
    )
    token = login.json()["meta"]["session_token"]

    _run(str(user.public_id), "--noinput", "--confirm", EMAIL)

    me = Client().get("/api/v1/users/me/", HTTP_X_SESSION_TOKEN=token)
    assert me.status_code == HTTPStatus.UNAUTHORIZED


@pytest.mark.parametrize("error", [ProtectedError, RestrictedError])
def test_protected_rows_stop_the_delete(error: type[Exception]) -> None:
    # PROTECT raises ProtectedError, RESTRICT raises RestrictedError (which
    # is not a subclass of it); both keep the user.
    user = _user_with_history()

    with (
        mock.patch(
            "apps.users.management.commands.hard_delete_user.Collector.collect",
            side_effect=error("Invoice.customer", set()),
        ),
        pytest.raises(CommandError, match="must go first"),
    ):
        _run(str(user.public_id), "--noinput", "--confirm", EMAIL)

    assert User.objects.filter(pk=user.pk).exists()
