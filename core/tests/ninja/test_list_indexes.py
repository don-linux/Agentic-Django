# DJ008: test-only models.
# ruff: noqa: DJ008
"""Every list endpoint orders by an indexed column (docs/ARCHITECTURE.md#indexes)."""

from __future__ import annotations

from typing import Any

import pytest
from django.db import models
from django.test.utils import isolate_apps

from apps.users.selectors import user_list
from apps.users.tests.factories import UserFactory

DOCS = "docs/ARCHITECTURE.md#indexes"


def _is_indexed(model: type[models.Model], name: str) -> bool:
    if name == "pk":
        return True
    field = model._meta.get_field(name)
    if (
        getattr(field, "primary_key", False)
        or getattr(field, "unique", False)
        or getattr(field, "db_index", False)
        or isinstance(field, models.ForeignKey)
    ):
        return True
    leading = {
        index.fields[0].lstrip("-") for index in model._meta.indexes if index.fields
    }
    leading |= {
        constraint.fields[0]
        for constraint in model._meta.constraints
        # Expression-only constraints (UniqueConstraint(Lower("email"))) have no fields.
        if isinstance(constraint, models.UniqueConstraint) and constraint.fields
    }
    return name in leading


def ordering_problems(
    queryset: models.QuerySet[Any],
    *,
    label: str,
    extra_fields: list[str] | None = None,
) -> list[str]:
    """Return "<label>.<field>" for each unindexed default or extra ordering."""
    model = queryset.model
    raw_ordering = queryset.query.order_by or model._meta.ordering or ()
    fields = [str(field) for field in raw_ordering[:1]]
    fields += [str(field) for field in extra_fields or []]
    return [
        f"{label}.{field.lstrip('-')}"
        for field in fields
        if field != "__all__" and not _is_indexed(model, field.lstrip("-"))
    ]


@pytest.mark.django_db
def test_list_endpoints_order_by_indexed_columns() -> None:
    user = UserFactory()
    queryset = user_list(user=user)
    problems = ordering_problems(queryset, label="users_list")
    assert problems == [], f"Unindexed list ordering {problems}; see {DOCS}"


@isolate_apps("core")
def test_ordering_check_flags_unindexed_columns() -> None:
    class Item(models.Model):
        title = models.CharField(max_length=50)
        rank = models.IntegerField()
        code = models.CharField(max_length=10, unique=True)

        class Meta:
            indexes = [models.Index(fields=["-rank", "-id"], name="core_item_rank_idx")]

    manager = Item._default_manager
    assert (
        ordering_problems(
            manager.order_by("-id"), label="Item", extra_fields=["rank", "code"]
        )
        == []
    )
    assert ordering_problems(
        manager.order_by("title"), label="Item", extra_fields=["rank"]
    ) == ["Item.title"]
    assert ordering_problems(
        manager.order_by("id"), label="Item", extra_fields=["title"]
    ) == ["Item.title"]
