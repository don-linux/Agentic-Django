import uuid
from typing import Any
from typing import ClassVar

from django.contrib.auth.models import AbstractUser
from django.db.models import CharField
from django.db.models import EmailField
from django.db.models import ProtectedError
from django.db.models import UniqueConstraint
from django.db.models import UUIDField
from django.db.models.functions import Lower

from .managers import USER_DELETE_BLOCKED
from .managers import UserManager


class User(AbstractUser):
    """Email-login user. Signup goes through allauth headless."""

    # First and last name do not cover name patterns around the globe
    name = CharField("Name", blank=True, max_length=255)
    first_name = None  # type: ignore[assignment]
    last_name = None  # type: ignore[assignment]
    email = EmailField("email address", unique=True)
    username = None  # type: ignore[assignment]
    # The API identifier, declared here: User subclasses AbstractUser, so it cannot
    # inherit core.models.PublicIdModel. The integer pk stays internal.
    public_id = UUIDField(default=uuid.uuid7, unique=True, editable=False)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects: ClassVar[UserManager] = UserManager()

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"
        constraints = [
            UniqueConstraint(Lower("email"), name="users_user_email_ci_uniq"),
        ]

    def clean(self) -> None:
        super().clean()
        self.email = self.__class__.objects.normalize_email(self.email)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ProtectedError(USER_DELETE_BLOCKED, {self})

    def save(self, *args: Any, **kwargs: Any) -> None:
        # Every write path (admin, ORM, allauth) stores the key that the unique
        # constraint and the login lookup use.
        self.email = self.__class__.objects.normalize_email(self.email)
        if not self.email:
            msg = "The given email must be set"
            raise ValueError(msg)
        super().save(*args, **kwargs)

    def get_full_name(self) -> str:
        return self.name.strip()

    def get_short_name(self) -> str:
        return self.name.strip()
