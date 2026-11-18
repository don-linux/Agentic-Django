from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING
from typing import Any

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import UserManager as DjangoUserManager
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
    def delete(self) -> tuple[int, dict[str, int]]:
        raise ProtectedError(USER_DELETE_BLOCKED, set(self))


class UserManager(DjangoUserManager["User"]):
    use_in_migrations = True

    def get_queryset(self) -> UserQuerySet:
        return UserQuerySet(self.model, using=self._db)

    @classmethod
    def normalize_email(cls, email: str | None) -> str:
        """
        NFKC-normalize, strip and lowercase the whole address.

        Django's default lowercases only the domain.
        """
        if not email:
            return ""
        return unicodedata.normalize("NFKC", email).strip().lower()

    def _create_user(
        self, email: str, password: str | None, **extra_fields: Any
    ) -> User:
        email = self.normalize_email(email)
        if not email:
            msg = "The given email must be set"
            raise ValueError(msg)
        user = self.model(email=email, **extra_fields)
        user.password = make_password(password)
        user.save(using=self._db)
        return user

    def create_user(  # type: ignore[override]
        self, email: str, password: str | None = None, **extra_fields: Any
    ) -> User:
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(  # type: ignore[override]
        self, email: str, password: str | None = None, **extra_fields: Any
    ) -> User:
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            msg = "Superuser must have is_staff=True."
            raise ValueError(msg)
        if extra_fields.get("is_superuser") is not True:
            msg = "Superuser must have is_superuser=True."
            raise ValueError(msg)

        return self._create_user(email, password, **extra_fields)
