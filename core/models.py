from __future__ import annotations

import uuid
from typing import TYPE_CHECKING
from typing import Any

from django.db import models
from django.db.models.functions import Now

if TYPE_CHECKING:
    from datetime import date
    from datetime import datetime

    from django.db.models.expressions import Combinable

    type _DateTimeIn = str | datetime | date | Combinable


class PublicIdModel(models.Model):
    """
    A public UUIDv7 identifier for the API; the integer pk stays internal.

    See docs/ARCHITECTURE.md#layers.
    """

    # Field annotations repeat django-stubs' set/get types so pyright sees them
    # (it does not run the mypy plugin, which also checks them against null=).
    # See docs/typing.md#model-field-annotations.
    public_id: models.UUIDField[str | uuid.UUID, uuid.UUID] = models.UUIDField(
        default=uuid.uuid7, unique=True, editable=False
    )

    class Meta:
        abstract = True


class BaseQuerySet[M: models.Model](models.QuerySet[M]):
    """
    A QuerySet whose bulk `update()` also bumps `updated_at`.

    `bulk_update()` does not: include "updated_at" in its fields.
    See docs/ARCHITECTURE.md#layers.
    """

    def update(self, **kwargs: Any) -> int:
        # Model._meta is Django's documented Meta API.
        fields = {field.name for field in self.model._meta.concrete_fields}  # noqa: SLF001
        if "updated_at" in fields and "updated_at" not in kwargs:
            kwargs["updated_at"] = Now()
        return super().update(**kwargs)


BaseManager = models.Manager.from_queryset(BaseQuerySet)


class BaseModel(PublicIdModel):
    """
    Shared fields for domain models: `public_id`, `created_at`, `updated_at`.

    Both timestamps come from the database clock (`Now()`). User keeps AbstractUser
    and declares its own `public_id`. Custom managers derive from `BaseManager`.
    See docs/ARCHITECTURE.md#layers.
    """

    created_at: models.DateTimeField[_DateTimeIn, datetime] = models.DateTimeField(
        db_default=Now(), editable=False
    )
    updated_at: models.DateTimeField[_DateTimeIn, datetime] = models.DateTimeField(
        db_default=Now(), editable=False
    )

    objects = BaseManager()

    class Meta(PublicIdModel.Meta):
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields_set = set(update_fields)
            if not update_fields_set:
                # Django skips the write, so nothing would refresh an assigned
                # Now(): leave updated_at untouched.
                super().save(*args, **kwargs)
                return
            kwargs["update_fields"] = [*update_fields_set | {"updated_at"}]
        self.updated_at = Now()
        super().save(*args, **kwargs)
