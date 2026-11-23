from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING
from typing import Any
from typing import override

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.hashers import make_password
from django.db.models import ProtectedError
from django.db.models import QuerySet

if TYPE_CHECKING:
    from .models import User

# Deleting a user cascades into admin.LogEntry, allauth and MFA rows. Users are
# deactivated or anonymized instead; only `manage.py hard_delete_user` removes
# the row (docs/user-deletion/).
USER_DELETE_BLOCKED = (
    "Users are not deleted through the ORM: deactivate or anonymize them "
    "(apps.users.services), or run `manage.py hard_delete_user`."
)


class UserQuerySet(QuerySet["User"]):
    @override
    def delete(self) -> tuple[int, dict[str, int]]:
        raise ProtectedError(USER_DELETE_BLOCKED, set(self))


class UserManager(BaseUserManager["User"]):
    """
    The email-login manager that Django's guide writes for a model without username.

    Django's own UserManager expects a `username` field, so its creators, async
    ones included, cannot build this User (docs/decisions/0014).
    """

    use_in_migrations = True

    @override
    def get_queryset(self) -> UserQuerySet:
        return UserQuerySet(self.model, using=self._db)

    @override
    @classmethod
    def normalize_email(cls, email: str | None) -> str:
        """
        NFKC-normalize, strip and lowercase the whole address.

        Django's default lowercases only the domain.
        """
        if not email:
            return ""
        return unicodedata.normalize("NFKC", email).strip().lower()

    def create_user(
        self, email: str, password: str | None = None, **extra_fields: Any
    ) -> User:
        user = self._build_user(email, password, **_regular_flags(extra_fields))
        user.save(using=self._db)
        return user

    async def acreate_user(
        self, email: str, password: str | None = None, **extra_fields: Any
    ) -> User:
        user = self._build_user(email, password, **_regular_flags(extra_fields))
        await user.asave(using=self._db)
        return user

    def create_superuser(
        self, email: str, password: str | None = None, **extra_fields: Any
    ) -> User:
        user = self._build_user(email, password, **_superuser_flags(extra_fields))
        user.save(using=self._db)
        return user

    async def acreate_superuser(
        self, email: str, password: str | None = None, **extra_fields: Any
    ) -> User:
        user = self._build_user(email, password, **_superuser_flags(extra_fields))
        await user.asave(using=self._db)
        return user

    def _build_user(
        self, email: str, password: str | None, **extra_fields: Any
    ) -> User:
        """
        Return an unsaved user with a normalized email and a hashed password.

        No query here: the async creators run it on the event loop. Hashing blocks
        the loop for a moment, as it does in Django's own UserManager.
        """
        email = self.normalize_email(email)
        if not email:
            msg = "The given email must be set"
            raise ValueError(msg)
        user = self.model(email=email, **extra_fields)
        user.password = make_password(password)
        return user


def _regular_flags(extra_fields: dict[str, Any]) -> dict[str, Any]:
    extra_fields.setdefault("is_staff", False)
    extra_fields.setdefault("is_superuser", False)
    return extra_fields


def _superuser_flags(extra_fields: dict[str, Any]) -> dict[str, Any]:
    extra_fields.setdefault("is_staff", True)
    extra_fields.setdefault("is_superuser", True)
    if extra_fields.get("is_staff") is not True:
        msg = "Superuser must have is_staff=True."
        raise ValueError(msg)
    if extra_fields.get("is_superuser") is not True:
        msg = "Superuser must have is_superuser=True."
        raise ValueError(msg)
    return extra_fields
