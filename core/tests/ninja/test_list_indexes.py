# SLF001: Model._meta is Django's documented Meta API.
# ruff: noqa: SLF001
"""Every list endpoint orders by an indexed column (docs/ARCHITECTURE.md#indexes)."""

from __future__ import annotations

import pytest
from django.db import models

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
    return name in leading


@pytest.mark.django_db
def test_list_endpoints_order_by_indexed_columns() -> None:
    user = UserFactory()
    queryset = user_list(user=user)
    model = queryset.model
    raw_ordering = queryset.query.order_by or model._meta.ordering or ()
    ordering = [str(field) for field in raw_ordering]
    problems = [
        f"users_list.{field.lstrip('-')}"
        for field in ordering
        if field != "__all__" and not _is_indexed(model, field.lstrip("-"))
    ]
    assert problems == [], f"Unindexed list ordering {problems}; see {DOCS}"
