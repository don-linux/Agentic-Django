from io import StringIO

import pytest
from django.contrib.auth.hashers import make_password
from django.core.management import call_command
from django.db import IntegrityError

from apps.users.adapters import AccountAdapter
from apps.users.models import User


@pytest.mark.django_db
class TestUserManager:
    def test_create_user_normalizes_email(self) -> None:
        user = User.objects.create_user(
            email="  John.Doe@Example.COM  ",
            password="something-r@nd0m!",  # noqa: S106
        )
        assert user.email == "john.doe@example.com"
        assert not user.is_staff
        assert not user.is_superuser
        assert user.check_password("something-r@nd0m!")
        assert user.username is None

    @pytest.mark.parametrize("bad_email", ["", "   "])
    def test_create_user_rejects_empty_email(self, bad_email: str) -> None:
        with pytest.raises(ValueError, match="The given email must be set"):
            User.objects.create_user(email=bad_email, password="something-r@nd0m!")  # noqa: S106

    def test_case_insensitive_email_constraint(self) -> None:
        User.objects.create_user(
            email="ada@example.com",
            password="something-r@nd0m!",  # noqa: S106
        )
        # Bypass normalize_email to test the DB Lower("email") constraint.
        duplicate = User(email="ADA@example.com")
        with pytest.raises(IntegrityError):
            duplicate.save()

    def test_create_superuser(self) -> None:
        user = User.objects.create_superuser(
            email="Admin@Example.com",
            password="something-r@nd0m!",  # noqa: S106
        )
        assert user.email == "admin@example.com"
        assert user.is_staff
        assert user.is_superuser
        assert user.username is None

    def test_create_superuser_validates_flags_and_rejects_username(self) -> None:
        with pytest.raises(ValueError, match="is_staff=True"):
            User.objects.create_superuser(
                email="nostaff@example.com",
                password="something-r@nd0m!",  # noqa: S106
                is_staff=False,
            )
        with pytest.raises(ValueError, match="is_superuser=True"):
            User.objects.create_superuser(
                email="nosuper@example.com",
                password="something-r@nd0m!",  # noqa: S106
                is_superuser=False,
            )
        with pytest.raises(TypeError):
            User.objects.create_superuser(
                email="extra@example.com",
                password="something-r@nd0m!",  # noqa: S106
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
        user.set_password("Correct-H0rse-Battery")
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
        User.objects.create_user("twin-ascii@example.com", "Correct-H0rse-Battery")
        twin = User(email=f"{_fullwidth('twin')}-ascii@example.com")
        twin.set_password("Correct-H0rse-Battery")

        with pytest.raises(IntegrityError):
            twin.save()

    def test_save_refuses_an_empty_email(self) -> None:
        with pytest.raises(ValueError, match="email must be set"):
            User(email="  ").save()

    def test_the_account_adapter_normalizes_signup_and_login_input(self) -> None:
        raw = f"  {_fullwidth('M')}ixed@Example.COM "

        assert AccountAdapter().clean_email(raw) == "mixed@example.com"
