from __future__ import annotations

from io import StringIO
from typing import TYPE_CHECKING

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.hashers import make_password
from django.core.management import call_command
from django.db import IntegrityError

from apps.users.adapters import AccountAdapter
from apps.users.models import User
from apps.users.selectors import USER_NAMESPACE
from core.cache import build_key
from core.tests.credentials import PASSWORD

if TYPE_CHECKING:
    from conftest import CacheWrites


@pytest.mark.parametrize("name", ["username", "first_name", "last_name"])
def test_user_has_no_username_or_split_name(name: str) -> None:
    # Login is by email and the name is one `name` field (docs/decisions/0014).
    assert not hasattr(User, name)


@pytest.mark.django_db
class TestUserManager:
    def test_create_user_normalizes_email(self) -> None:
        user = User.objects.create_user(
            email="  John.Doe@Example.COM  ",
            password=PASSWORD,
        )
        assert user.email == "john.doe@example.com"
        assert not user.is_staff
        assert not user.is_superuser
        assert user.check_password(PASSWORD)
        assert not hasattr(user, "username")

    @pytest.mark.parametrize("bad_email", ["", "   "])
    def test_create_user_rejects_empty_email(self, bad_email: str) -> None:
        with pytest.raises(ValueError, match="The given email must be set"):
            User.objects.create_user(email=bad_email, password=PASSWORD)

    def test_case_insensitive_email_constraint(self) -> None:
        User.objects.create_user(
            email="ada@example.com",
            password=PASSWORD,
        )
        # Bypass normalize_email to test the DB Lower("email") constraint.
        duplicate = User(email="ADA@example.com")
        with pytest.raises(IntegrityError):
            duplicate.save()

    def test_create_superuser(self) -> None:
        user = User.objects.create_superuser(
            email="Admin@Example.com",
            password=PASSWORD,
        )
        assert user.email == "admin@example.com"
        assert user.is_staff
        assert user.is_superuser
        assert not hasattr(user, "username")

    def test_create_superuser_validates_flags_and_rejects_username(self) -> None:
        with pytest.raises(ValueError, match="is_staff=True"):
            User.objects.create_superuser(
                email="nostaff@example.com",
                password=PASSWORD,
                is_staff=False,
            )
        with pytest.raises(ValueError, match="is_superuser=True"):
            User.objects.create_superuser(
                email="nosuper@example.com",
                password=PASSWORD,
                is_superuser=False,
            )
        with pytest.raises(TypeError):
            User.objects.create_superuser(
                email="extra@example.com",
                password=PASSWORD,
                username="ignored",
            )

    def test_get_full_name_and_short_name(self) -> None:
        named = User(email="ada@example.com", name="  Ada Lovelace  ")
        unnamed = User(email="anon@example.com")

        assert named.get_full_name() == "Ada Lovelace"
        assert named.get_short_name() == "Ada Lovelace"
        assert unnamed.get_full_name() == ""
        assert unnamed.get_short_name() == ""


@pytest.mark.django_db
class TestAsyncUserManager:
    """
    The async creators, called the way Django's TestCase runs an async test.

    pytest-django runs no `async def` test, and `asyncio.run` would save on another
    thread and connection, outside the test transaction. See
    docs/development.md#async-code.
    """

    def test_acreate_user_normalizes_email_and_stores_password(self) -> None:
        user = async_to_sync(User.objects.acreate_user)(
            email="  John.Doe@Example.COM  ",
            password=PASSWORD,
        )

        stored = User.objects.get(pk=user.pk)
        assert stored.email == "john.doe@example.com"
        assert not stored.is_staff
        assert not stored.is_superuser
        assert stored.check_password(PASSWORD)

    @pytest.mark.parametrize("bad_email", ["", "   "])
    def test_acreate_user_rejects_empty_email(self, bad_email: str) -> None:
        with pytest.raises(ValueError, match="The given email must be set"):
            async_to_sync(User.objects.acreate_user)(
                email=bad_email,
                password=PASSWORD,
            )

        assert not User.objects.exists()

    def test_acreate_user_honors_extra_fields(self) -> None:
        user = async_to_sync(User.objects.acreate_user)(
            email="staff@example.com",
            password=PASSWORD,
            name="Ada",
            is_staff=True,
        )

        stored = User.objects.get(pk=user.pk)
        assert stored.name == "Ada"
        assert stored.is_staff is True
        assert stored.is_superuser is False

    def test_acreate_superuser_sets_flags_and_password(self) -> None:
        user = async_to_sync(User.objects.acreate_superuser)(
            email="Admin@Example.com",
            password=PASSWORD,
        )

        stored = User.objects.get(pk=user.pk)
        assert stored.email == "admin@example.com"
        assert stored.is_staff
        assert stored.is_superuser
        assert stored.check_password(PASSWORD)

    def test_acreate_superuser_validates_flags_and_rejects_username(self) -> None:
        acreate_superuser = async_to_sync(User.objects.acreate_superuser)
        with pytest.raises(ValueError, match="is_staff=True"):
            acreate_superuser(
                email="nostaff@example.com",
                password=PASSWORD,
                is_staff=False,
            )
        with pytest.raises(ValueError, match="is_superuser=True"):
            acreate_superuser(
                email="nosuper@example.com",
                password=PASSWORD,
                is_superuser=False,
            )
        with pytest.raises(TypeError):
            acreate_superuser(
                email="extra@example.com",
                password=PASSWORD,
                username="ignored",
            )

        assert not User.objects.exists()

    def test_acreate_user_invalidates_cached_users(
        self, cache_writes: CacheWrites
    ) -> None:
        # The key of a fixed part changes exactly when the namespace version does.
        before = build_key(USER_NAMESPACE, "probe")

        with cache_writes():
            async_to_sync(User.objects.acreate_user)(
                email="cached@example.com",
                password=PASSWORD,
            )

        assert build_key(USER_NAMESPACE, "probe") != before


@pytest.mark.django_db
def test_createsuperuser_command() -> None:
    out = StringIO()
    command_result = call_command(
        "createsuperuser",
        "--email",
        "Henry@Example.com",
        interactive=False,
        stdout=out,
    )

    assert command_result is None
    assert out.getvalue() == "Superuser created successfully.\n"
    user = User.objects.get(email="henry@example.com")
    assert not user.has_usable_password()


def _fullwidth(ascii_text: str) -> str:
    # NFKC turns these fullwidth letters back into ASCII.
    return "".join(chr(ord(char) + 0xFEE0) for char in ascii_text)


@pytest.mark.django_db
class TestEmailNormalizedOnEveryWrite:
    def test_save_normalizes_the_email(self) -> None:
        user = User(email="  Foo.Bar@Example.COM  ")
        user.set_password(PASSWORD)
        user.save()

        user.refresh_from_db()
        assert user.email == "foo.bar@example.com"

    def test_objects_create_normalizes_the_email(self) -> None:
        user = User.objects.create(
            email="Raw.Create@Example.com", password=make_password(None)
        )

        user.refresh_from_db()
        assert user.email == "raw.create@example.com"

    def test_a_fullwidth_twin_collides_with_the_normalized_email(self) -> None:
        User.objects.create_user("twin-ascii@example.com", PASSWORD)
        twin = User(email=f"{_fullwidth('twin')}-ascii@example.com")
        twin.set_password(PASSWORD)

        with pytest.raises(IntegrityError):
            twin.save()

    def test_save_refuses_an_empty_email(self) -> None:
        with pytest.raises(ValueError, match="email must be set"):
            User(email="  ").save()

    def test_the_account_adapter_normalizes_signup_and_login_input(self) -> None:
        raw = f"  {_fullwidth('M')}ixed@Example.COM "

        assert AccountAdapter().clean_email(raw) == "mixed@example.com"
