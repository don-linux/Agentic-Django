from __future__ import annotations

import secrets
from typing import TYPE_CHECKING
from typing import Any

from factory.django import DjangoModelFactory
from factory.faker import Faker

from apps.users.models import User


def _apply_password(user: User, extracted: str | None, *, save: bool) -> None:
    if extracted is not None:
        user.set_password(extracted)
    elif not user.password:
        user.set_password(secrets.token_urlsafe(32))
    else:
        return
    if save:
        user.save(update_fields=["password"])


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

    @classmethod
    def _build(cls, model_class: type[User], *args: Any, **kwargs: Any) -> User:
        # factory-boy's @post_generation calls the hook with the model instance
        # as "self". Annotating that parameter as User is an incompatible
        # method type on UserFactory, so password is applied in _build/_create.
        password = kwargs.pop("password", None)
        user = super()._build(model_class, *args, **kwargs)
        if not isinstance(password, str) and password is not None:
            password = None
        _apply_password(user, password, save=False)
        return user

    @classmethod
    def _create(cls, model_class: type[User], *args: Any, **kwargs: Any) -> User:
        password = kwargs.pop("password", None)
        user = super()._create(model_class, *args, **kwargs)
        if not isinstance(password, str) and password is not None:
            password = None
        _apply_password(user, password, save=True)
        return user

    # factory-boy's metaclass consumes Meta (DjangoModelFactory.Meta does not
    # exist at run time), so this Meta cannot subclass it; Pyright 1.1.414 reads
    # the nested class in factory/django.py and flags the override, by design
    # (microsoft/pyright#6814).
    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = User
        django_get_or_create = ["email"]
        skip_postgeneration_save = True
