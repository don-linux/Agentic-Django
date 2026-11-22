from http import HTTPStatus

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from apps.users.models import User
from apps.users.selectors import user_get
from apps.users.services import user_reactivate
from apps.users.tests.factories import UserFactory
from core.tests.credentials import PASSWORD

pytestmark = pytest.mark.django_db


class TestUserAdmin:
    def test_changelist(self, admin_client) -> None:
        url = reverse("admin:users_user_changelist")
        response = admin_client.get(url)
        assert response.status_code == HTTPStatus.OK

    def test_search_by_name_and_email(self, admin_client) -> None:
        alice = UserFactory(email="alice-search@example.com", name="Alice Lovelace")
        bob = UserFactory(email="bob-search@example.com", name="Bob Babbage")
        url = reverse("admin:users_user_changelist")

        by_name = admin_client.get(url, data={"q": "Lovelace"})
        assert by_name.status_code == HTTPStatus.OK
        assert alice.email.encode() in by_name.content
        assert bob.email.encode() not in by_name.content

        by_email = admin_client.get(url, data={"q": "bob-search@example.com"})
        assert by_email.status_code == HTTPStatus.OK
        assert bob.email.encode() in by_email.content
        assert alice.email.encode() not in by_email.content

    def test_add_creates_normalized_user_and_syncs_email_address(
        self,
        admin_client,
    ) -> None:
        url = reverse("admin:users_user_add")
        response = admin_client.get(url)
        assert response.status_code == HTTPStatus.OK
        assert b"usable_password" in response.content

        response = admin_client.post(
            url,
            data={
                "email": "New-Admin@Example.com",
                "usable_password": "true",
                "password1": PASSWORD,
                "password2": PASSWORD,
            },
        )
        assert response.status_code == HTTPStatus.FOUND
        created = User.objects.get(email="new-admin@example.com")
        assert created.check_password(PASSWORD)
        assert EmailAddress.objects.filter(
            user=created,
            email="new-admin@example.com",
            primary=True,
            verified=True,
        ).exists()

    def test_add_supports_unusable_password(self, admin_client) -> None:
        url = reverse("admin:users_user_add")
        response = admin_client.post(
            url,
            data={
                "email": "no-pass@example.com",
                "usable_password": "false",
            },
        )
        assert response.status_code == HTTPStatus.FOUND
        created = User.objects.get(email="no-pass@example.com")
        assert not created.has_usable_password()

    def test_change_email_replaces_primary_email_address(self, admin_client) -> None:
        target = UserFactory(email="old-mail@example.com", name="Target")
        EmailAddress.objects.create(
            user=target,
            email="old-mail@example.com",
            primary=True,
            verified=True,
        )
        url = reverse("admin:users_user_change", kwargs={"object_id": target.pk})
        response = admin_client.post(
            url,
            data={
                "email": "New-Mail@Example.com",
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
        assert target.email == "new-mail@example.com"
        assert not EmailAddress.objects.filter(email="old-mail@example.com").exists()
        assert EmailAddress.objects.filter(
            user=target,
            email="new-mail@example.com",
            primary=True,
            verified=True,
        ).exists()

    def test_a_change_in_the_admin_reaches_the_cached_user(
        self,
        admin_client,
        cache_writes,
    ) -> None:
        # The admin saves the model itself, without a service: the post_save
        # receiver is what invalidates (apps/users/signals.py).
        target = UserFactory(email="cached@example.com", name="Before")
        with cache_writes():
            user_get(public_id=target.public_id)
        url = reverse("admin:users_user_change", kwargs={"object_id": target.pk})

        with cache_writes():
            response = admin_client.post(
                url,
                data={
                    "email": "cached@example.com",
                    "name": "After",
                    "is_active": "on",
                    "date_joined_0": "2026-01-01",
                    "date_joined_1": "00:00:00",
                    "initial-date_joined_0": "2026-01-01",
                    "initial-date_joined_1": "00:00:00",
                },
            )

        assert response.status_code == HTTPStatus.FOUND
        assert user_get(public_id=target.public_id).name == "After"

    def test_view_user(self, admin_client) -> None:
        user = User.objects.get(email="admin@example.com")
        url = reverse("admin:users_user_change", kwargs={"object_id": user.pk})
        response = admin_client.get(url)
        assert response.status_code == HTTPStatus.OK


class TestUserDeactivationInAdmin:
    def test_users_cannot_be_deleted(self, admin_client, user: User) -> None:
        response = admin_client.get(reverse("admin:users_user_delete", args=[user.pk]))

        assert response.status_code == HTTPStatus.FORBIDDEN
        assert User.objects.filter(pk=user.pk).exists()

    def test_change_email_promotes_an_existing_non_primary_address(
        self,
        admin_client,
    ) -> None:
        target = UserFactory(email="old-primary@example.com", name="Target")
        EmailAddress.objects.create(
            user=target,
            email="old-primary@example.com",
            primary=True,
            verified=True,
        )
        EmailAddress.objects.create(
            user=target,
            email="secondary@example.com",
            primary=False,
            verified=False,
        )
        url = reverse("admin:users_user_change", kwargs={"object_id": target.pk})
        response = admin_client.post(
            url,
            data={
                "email": "Secondary@Example.com",
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
        assert not EmailAddress.objects.filter(email="old-primary@example.com").exists()
        promoted = EmailAddress.objects.get(user=target, email="secondary@example.com")
        assert promoted.primary is True
        # Promoting an unverified address does not verify it: only a new
        # address the admin types is created verified.
        assert promoted.verified is False

    def test_delete_selected_is_not_offered(self, admin_client) -> None:
        response = admin_client.get(reverse("admin:users_user_changelist"))

        actions = [
            name for name, _ in response.context["action_form"].fields["action"].choices
        ]
        assert "delete_selected" not in actions
        assert {"deactivate_users", "reactivate_users"} <= set(actions)

    def test_deactivate_action(self, admin_client, user: User) -> None:
        response = admin_client.post(
            reverse("admin:users_user_changelist"),
            {"action": "deactivate_users", "_selected_action": [user.pk]},
        )

        assert response.status_code == HTTPStatus.FOUND
        user.refresh_from_db()
        assert user.is_active is False

    def test_deactivate_action_skips_the_acting_admin(
        self, admin_client, admin_user
    ) -> None:
        admin_client.post(
            reverse("admin:users_user_changelist"),
            {"action": "deactivate_users", "_selected_action": [admin_user.pk]},
        )

        admin_user.refresh_from_db()
        assert admin_user.is_active is True

    def test_reactivate_action(self, admin_client, user: User) -> None:
        User.objects.filter(pk=user.pk).update(is_active=False)

        admin_client.post(
            reverse("admin:users_user_changelist"),
            {"action": "reactivate_users", "_selected_action": [user.pk]},
        )

        user.refresh_from_db()
        assert user.is_active is True


def _staff_with_change_user(email: str) -> User:
    staff = UserFactory(email=email, is_staff=True, is_superuser=False)
    staff.user_permissions.add(
        Permission.objects.get(content_type__app_label="users", codename="change_user")
    )
    return staff


def _change_data(user: User, **overrides: str) -> dict[str, str]:
    data = {
        "email": user.email,
        "name": user.name or "",
        "is_active": "on",
        "date_joined_0": "2026-01-01",
        "date_joined_1": "00:00:00",
        "initial-date_joined_0": "2026-01-01",
        "initial-date_joined_1": "00:00:00",
    }
    data.update(overrides)
    return data


class TestStaffWithoutSuperuser:
    """Staff with change_user cannot grant privileges or touch privileged users."""

    def test_privilege_fields_are_read_only(self, client) -> None:
        staff = _staff_with_change_user("staff-ro@example.com")
        target = UserFactory(email="target-ro@example.com")
        client.force_login(staff)

        response = client.get(
            reverse("admin:users_user_change", kwargs={"object_id": target.pk})
        )

        assert response.status_code == HTTPStatus.OK
        form_fields = response.context["adminform"].form.fields
        for name in ("is_staff", "is_superuser", "groups", "user_permissions"):
            assert name not in form_fields

    def test_cannot_promote_a_user(self, client) -> None:
        staff = _staff_with_change_user("staff-promote@example.com")
        target = UserFactory(email="target-promote@example.com")
        client.force_login(staff)

        client.post(
            reverse("admin:users_user_change", kwargs={"object_id": target.pk}),
            data=_change_data(target, is_staff="on", is_superuser="on"),
        )

        target.refresh_from_db()
        assert target.is_staff is False
        assert target.is_superuser is False

    @pytest.mark.parametrize("role", ["staff", "superuser"])
    def test_cannot_change_a_privileged_user(self, client, role: str) -> None:
        staff = _staff_with_change_user("staff-change@example.com")
        target = UserFactory(
            email="privileged@example.com",
            is_staff=True,
            is_superuser=role == "superuser",
        )
        client.force_login(staff)
        url = reverse("admin:users_user_change", kwargs={"object_id": target.pk})

        response = client.post(
            url, data=_change_data(target, email="stolen@example.com")
        )

        target.refresh_from_db()
        assert target.email == "privileged@example.com"
        assert target.is_superuser is (role == "superuser")
        assert response.status_code in {HTTPStatus.OK, HTTPStatus.FORBIDDEN}

    def test_cannot_promote_itself(self, client) -> None:
        staff = _staff_with_change_user("staff-self@example.com")
        client.force_login(staff)

        client.post(
            reverse("admin:users_user_change", kwargs={"object_id": staff.pk}),
            data=_change_data(staff, is_superuser="on"),
        )

        staff.refresh_from_db()
        assert staff.is_superuser is False

    def test_deactivate_skips_privileged_users(self, client) -> None:
        staff = _staff_with_change_user("staff-deactivate@example.com")
        superuser = UserFactory(
            email="super-keep@example.com", is_staff=True, is_superuser=True
        )
        other_staff = UserFactory(email="staff-keep@example.com", is_staff=True)
        regular = UserFactory(email="regular-gone@example.com")
        client.force_login(staff)

        response = client.post(
            reverse("admin:users_user_changelist"),
            {
                "action": "deactivate_users",
                "_selected_action": [superuser.pk, other_staff.pk, regular.pk],
            },
            follow=True,
        )

        for user in (superuser, other_staff, regular):
            user.refresh_from_db()
        assert superuser.is_active is True
        assert other_staff.is_active is True
        assert regular.is_active is False
        messages = [str(m) for m in response.context["messages"]]
        assert any("only a superuser can deactivate staff" in m for m in messages)

    def test_reactivate_skips_privileged_users(self, client) -> None:
        staff = _staff_with_change_user("staff-reactivate@example.com")
        superuser = UserFactory(
            email="inactive-root@example.com",
            is_staff=True,
            is_superuser=True,
            is_active=False,
        )
        other_staff = UserFactory(
            email="inactive-staff@example.com", is_staff=True, is_active=False
        )
        regular = UserFactory(email="inactive-regular@example.com", is_active=False)
        client.force_login(staff)

        response = client.post(
            reverse("admin:users_user_changelist"),
            {
                "action": "reactivate_users",
                "_selected_action": [superuser.pk, other_staff.pk, regular.pk],
            },
            follow=True,
        )

        for user in (superuser, other_staff, regular):
            user.refresh_from_db()
        assert superuser.is_active is False
        assert other_staff.is_active is False
        assert regular.is_active is True
        messages = [str(m) for m in response.context["messages"]]
        assert any("only a superuser can reactivate staff" in m for m in messages)

    def test_a_superuser_keeps_every_field(self, admin_client) -> None:
        target = UserFactory(email="target-super@example.com")

        response = admin_client.get(
            reverse("admin:users_user_change", kwargs={"object_id": target.pk})
        )

        form_fields = response.context["adminform"].form.fields
        assert {"is_staff", "is_superuser", "groups", "user_permissions"} <= set(
            form_fields
        )


class TestAdminActiveFlag:
    """Unticking is_active in the change form goes through the services."""

    @staticmethod
    def _login(user: User, password: str) -> str:
        response = Client().post(
            "/_allauth/app/v1/auth/login",
            {"email": user.email, "password": password},
            content_type="application/json",
        )
        assert response.status_code == HTTPStatus.OK, response.content
        return response.json()["meta"]["session_token"]

    @staticmethod
    def _me(token: str) -> int:
        return Client().get("/api/v1/users/me/", HTTP_X_SESSION_TOKEN=token).status_code

    def test_unticking_is_active_signs_the_user_out_for_good(
        self, admin_client
    ) -> None:
        password = PASSWORD
        target = UserFactory(email="form-deactivated@example.com", password=password)
        EmailAddress.objects.create(
            user=target, email=target.email, primary=True, verified=True
        )
        token = self._login(target, password)
        data = _change_data(target)
        del data["is_active"]

        response = admin_client.post(
            reverse("admin:users_user_change", kwargs={"object_id": target.pk}),
            data=data,
        )

        assert response.status_code == HTTPStatus.FOUND
        target.refresh_from_db()
        assert target.is_active is False
        assert self._me(token) == HTTPStatus.UNAUTHORIZED
        user_reactivate(user=target)
        assert self._me(token) == HTTPStatus.UNAUTHORIZED

    def test_an_admin_cannot_untick_their_own_account(
        self, admin_client, admin_user
    ) -> None:
        data = _change_data(admin_user, is_staff="on", is_superuser="on")
        del data["is_active"]

        response = admin_client.post(
            reverse("admin:users_user_change", kwargs={"object_id": admin_user.pk}),
            data=data,
            follow=True,
        )

        admin_user.refresh_from_db()
        assert admin_user.is_active is True
        messages = [str(m) for m in response.context["messages"]]
        assert "You cannot deactivate your own account." in messages
