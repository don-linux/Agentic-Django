from __future__ import annotations

import secrets
from typing import TYPE_CHECKING
from typing import Any

from factory import Faker
from factory import post_generation
from factory.django import DjangoModelFactory

from apps.users.models import User


class UserFactory(DjangoModelFactory[User]):
    email = Faker("email")
    name = Faker("name")

    if TYPE_CHECKING:

        def __new__(cls, *args: Any, **kwargs: Any) -> User: ...  # type: ignore[misc]

    @classmethod
    def _adjust_kwargs(cls, **kwargs):
        if "email" in kwargs and isinstance(kwargs["email"], str):
            kwargs["email"] = User.objects.normalize_email(kwargs["email"])
        return kwargs

    @post_generation
    def password(self: User, create: bool, extracted: str | None, **_kwargs):  # noqa: FBT001
        if extracted is not None:
            self.set_password(extracted)
        elif not self.password:
            self.set_password(secrets.token_urlsafe(32))
        else:
            return
        if create:
            self.save(update_fields=["password"])

    class Meta:
        model = User
        django_get_or_create = ["email"]
        skip_postgeneration_save = True
