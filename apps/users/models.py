import uuid
from typing import TYPE_CHECKING
from typing import ClassVar
from typing import override

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import PermissionsMixin
from django.db.models import BooleanField
from django.db.models import CharField
from django.db.models import DateTimeField
from django.db.models import EmailField
from django.db.models import ProtectedError
from django.db.models import UniqueConstraint
from django.db.models import UUIDField
from django.db.models.functions import Lower
from django.utils import timezone

from .managers import USER_DELETE_BLOCKED
from .managers import UserManager

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import date
    from datetime import datetime

    from allauth.account.models import EmailAddress
    from allauth.mfa.models import Authenticator
    from django.contrib.admin.models import LogEntry
    from django.db.models.base import ModelBase
    from django.db.models.expressions import Combinable
    from django_stubs_ext.db.models.manager import RelatedManager


class User(AbstractBaseUser, PermissionsMixin):
    """
    Email-login user. Signup goes through allauth headless.

    Built on AbstractBaseUser and PermissionsMixin, as Django's guide builds a user
    model without `username`, not on AbstractUser (docs/decisions/0014).
    """

    # The status fields of Django's AbstractUser, with its exact options, in the
    # order of migration 0001.
    is_staff: BooleanField[bool | Combinable, bool] = BooleanField(
        "staff status",
        default=False,
        help_text="Designates whether the user can log into this admin site.",
    )
    # django-stubs annotates AbstractBaseUser.is_active with this union, and pyright
    # requires a redeclared class variable to keep its declared type (docs/typing.md).
    is_active: bool | BooleanField[bool | Combinable, bool] = BooleanField(
        "active",
        default=True,
        help_text=(
            "Designates whether this user should be treated as active. "
            "Unselect this instead of deleting accounts."
        ),
    )
    date_joined: DateTimeField[str | datetime | date | Combinable, datetime] = (
        DateTimeField("date joined", default=timezone.now)
    )
    # One name field: first and last name do not cover name patterns around the
    # globe. Annotated for pyright, as in core/models.py (docs/typing.md).
    name: CharField[str | int | Combinable, str] = CharField(
        "Name", blank=True, max_length=255
    )
    email: EmailField[str | Combinable, str] = EmailField("email address", unique=True)
    # The API identifier (docs/decisions/0003); the integer pk stays internal.
    # Declared here rather than inherited from core.models.PublicIdModel, which
    # would add a third Meta parent for one field (docs/decisions/0014).
    public_id: UUIDField[str | uuid.UUID, uuid.UUID] = UUIDField(
        default=uuid.uuid7, unique=True, editable=False
    )

    # Reverse accessors. The django-stubs mypy plugin declares them; pyright does
    # not run the plugin, so they are declared here (django-stubs README, "Do I
    # need to annotate reverse relations?"). Annotations only: no runtime effect.
    emailaddress_set: RelatedManager[EmailAddress]
    authenticator_set: RelatedManager[Authenticator]
    logentry_set: RelatedManager[LogEntry]

    EMAIL_FIELD = "email"
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects: ClassVar[UserManager] = UserManager()

    # Extends the Meta of both abstract parents: with several, Django inherits only
    # the first one's unless Meta names them all (Django, "Meta inheritance").
    class Meta(AbstractBaseUser.Meta, PermissionsMixin.Meta):
        verbose_name = "user"
        verbose_name_plural = "users"
        constraints = [
            UniqueConstraint(Lower("email"), name="users_user_email_ci_uniq"),
        ]

    @override
    def clean(self) -> None:
        super().clean()
        self.email = self.__class__.objects.normalize_email(self.email)

    @override
    def delete(
        self, using: object = None, keep_parents: bool = False
    ) -> tuple[int, dict[str, int]]:
        raise ProtectedError(USER_DELETE_BLOCKED, {self})

    @override
    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        # Every write path (admin, ORM, allauth) stores the key that the unique
        # constraint and the login lookup use.
        self.email = self.__class__.objects.normalize_email(self.email)
        if not self.email:
            msg = "The given email must be set"
            raise ValueError(msg)
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )

    def get_full_name(self) -> str:
        return self.name.strip()

    def get_short_name(self) -> str:
        return self.name.strip()
