from __future__ import annotations

from typing import TYPE_CHECKING
from typing import override

from django.contrib import admin
from django.contrib import messages
from django.contrib.auth import admin as auth_admin
from django.contrib.auth.models import PermissionsMixin
from django.db import transaction

from .forms import UserAdminChangeForm
from .forms import UserAdminCreationForm
from .models import User
from .services import user_deactivate
from .services import user_primary_email_sync
from .services import user_reactivate

if TYPE_CHECKING:
    from django import forms
    from django.contrib.auth.base_user import AbstractBaseUser
    from django.contrib.auth.models import AnonymousUser
    from django.db.models import QuerySet
    from django.http import HttpRequest


def _is_superuser(user: AbstractBaseUser | AnonymousUser) -> bool:
    # HttpRequest.user is AbstractBaseUser | AnonymousUser in django-stubs 6.1.2.
    # is_superuser lives on PermissionsMixin (and AnonymousUser), not on
    # AbstractBaseUser, so pyright cannot see it on request.user.
    return isinstance(user, PermissionsMixin) and user.is_superuser


@admin.register(User)
class UserAdmin(auth_admin.UserAdmin):
    form = UserAdminChangeForm
    add_form = UserAdminCreationForm
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("name",)}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    list_display = ["email", "name", "is_active", "is_superuser"]
    list_filter = ["is_active", "is_staff", "is_superuser"]
    # Prefixes, not icontains: a search for "%text%" reads every row of a table
    # that grows (docs/ARCHITECTURE.md#performance).
    search_fields = ["^email", "^name"]
    # No COUNT(*) of the whole table on every changelist page.
    show_full_result_count = False
    actions = ["deactivate_users", "reactivate_users"]
    ordering = ["id"]
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "usable_password", "password1", "password2"),
            },
        ),
    )

    @override
    @transaction.atomic
    def save_model(
        self,
        request: HttpRequest,
        obj: User,
        form: forms.ModelForm[User],
        change: bool,
    ) -> None:
        # is_active goes through the services, which also sign the user out.
        is_active_changed = change and "is_active" in form.changed_data
        wanted_active = obj.is_active
        if is_active_changed and obj.pk == request.user.pk and not wanted_active:
            obj.is_active = True
            self.message_user(
                request,
                "You cannot deactivate your own account.",
                messages.WARNING,
            )
        super().save_model(request, obj, form, change)
        if is_active_changed and obj.pk != request.user.pk:
            if wanted_active:
                user_reactivate(user=obj)
            else:
                user_deactivate(user=obj)
        if not change or "email" in form.changed_data:
            user_primary_email_sync(user=obj)

    # Only a superuser grants or removes privileges, or touches a privileged user.
    _PRIVILEGE_FIELDS = ("is_staff", "is_superuser", "groups", "user_permissions")

    @override
    def get_readonly_fields(
        self, request: HttpRequest, obj: User | None = None
    ) -> tuple[str, ...] | list[str]:
        readonly = list(super().get_readonly_fields(request, obj))
        if not _is_superuser(request.user):
            readonly += [f for f in self._PRIVILEGE_FIELDS if f not in readonly]
        return readonly

    @override
    def has_change_permission(
        self, request: HttpRequest, obj: User | None = None
    ) -> bool:
        if not super().has_change_permission(request, obj):
            return False
        return (
            obj is None
            or _is_superuser(request.user)
            or not (obj.is_staff or obj.is_superuser)
        )

    # Users are deactivated or anonymized, never deleted: see
    # docs/user-deletion/README.md. Without delete permission the admin also
    # hides the "delete selected" action.
    @override
    def has_delete_permission(
        self, request: HttpRequest, obj: User | None = None
    ) -> bool:
        return False

    # permissions: a staff user who may only view users must not run them.
    @admin.action(description="Deactivate selected users", permissions=["change"])
    def deactivate_users(self, request: HttpRequest, queryset: QuerySet[User]) -> None:
        others = queryset.exclude(pk=request.user.pk) if request.user.pk else queryset
        if not _is_superuser(request.user):
            others = others.exclude(is_staff=True).exclude(is_superuser=True)
        count = 0
        for user in others:
            user_deactivate(user=user)
            count += 1
        if count < queryset.count():
            self.message_user(
                request,
                "Skipped your own account and any staff account: only a superuser "
                "can deactivate staff.",
                messages.WARNING,
            )
        self.message_user(request, f"Deactivated {count} user(s).", messages.SUCCESS)

    @admin.action(description="Reactivate selected users", permissions=["change"])
    def reactivate_users(self, request: HttpRequest, queryset: QuerySet[User]) -> None:
        others = queryset
        if not _is_superuser(request.user):
            others = others.exclude(is_staff=True).exclude(is_superuser=True)
        count = 0
        for user in others:
            user_reactivate(user=user)
            count += 1
        if count < queryset.count():
            self.message_user(
                request,
                "Skipped any staff account: only a superuser can reactivate staff.",
                messages.WARNING,
            )
        self.message_user(request, f"Reactivated {count} user(s).", messages.SUCCESS)
