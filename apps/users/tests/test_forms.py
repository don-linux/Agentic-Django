from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from apps.users.forms import UserAdminChangeForm
from apps.users.forms import UserAdminCreationForm
from apps.users.tests.factories import UserFactory

if TYPE_CHECKING:
    from apps.users.models import User

pytestmark = pytest.mark.django_db


class TestUserAdminCreationForm:
    @pytest.mark.parametrize("transform", [str.lower, str.upper])
    def test_duplicate_email_rejected_case_insensitively(
        self,
        user: User,
        transform,
    ) -> None:
        form = UserAdminCreationForm(
            {
                "email": transform(user.email),
                "usable_password": "true",
                "password1": "My_R@ndom-P@ssw0rd",
                "password2": "My_R@ndom-P@ssw0rd",
            },
        )

        assert not form.is_valid()
        assert len(form.errors) == 1
        assert "email" in form.errors
        assert form.errors["email"][0] == "A user with this email already exists."

    def test_valid_form_normalizes_email_to_lowercase(self) -> None:
        form = UserAdminCreationForm(
            {
                "email": "  New.Admin@Example.COM  ",
                "usable_password": "true",
                "password1": "My_R@ndom-P@ssw0rd",
                "password2": "My_R@ndom-P@ssw0rd",
            },
        )

        assert form.is_valid(), form.errors
        created = form.save()
        assert created.email == "new.admin@example.com"


class TestUserAdminChangeForm:
    def test_rejects_case_insensitive_collision_with_other_user(
        self,
        user: User,
    ) -> None:
        other = UserFactory(email="other@example.com")
        form = UserAdminChangeForm(
            data={
                "email": user.email.upper(),
                "name": "Updated",
                "date_joined_0": "2026-01-01",
                "date_joined_1": "00:00:00",
            },
            instance=other,
        )

        assert not form.is_valid()
        assert "email" in form.errors
        assert form.errors["email"][0] == "A user with this email already exists."
