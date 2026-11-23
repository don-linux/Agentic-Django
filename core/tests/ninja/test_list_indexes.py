# DJ008: test-only models.
# ruff: noqa: DJ008
"""Every list operation orders by an indexed column (docs/ARCHITECTURE.md#indexes).

LIST_SELECTORS maps each list operation of the API to its selector; a list
operation missing from it fails, so a new app cannot skip the check.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

import pytest
from django.db import models
from django.test.utils import isolate_apps

from apps.users.selectors import user_list
from apps.users.tests.factories import UserFactory
from core.api.api import api

if TYPE_CHECKING:
    from collections.abc import Callable

    from apps.users.models import User

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


# The selector behind each list operation of the API, by operationId. A new
# list operation fails test_every_list_operation_has_its_selector_here until
# its selector is added, once per ordering a client may choose.
LIST_SELECTORS: dict[str, Callable[[User], models.QuerySet[Any]]] = {
    "users_list": lambda user: user_list(user=user),
}


def test_every_list_operation_has_its_selector_here() -> None:
    paths = api.get_openapi_schema()["paths"]
    lists = {
        op["operationId"]
        for item in paths.values()
        for op in item.values()
        if op["operationId"].endswith("_list")
    }
    assert lists <= set(LIST_SELECTORS), sorted(lists - set(LIST_SELECTORS))


@pytest.mark.django_db
def test_list_endpoints_order_by_indexed_columns() -> None:
    user = UserFactory()
    problems = [
        problem
        for label, selector in LIST_SELECTORS.items()
        for problem in ordering_problems(selector(user), label=label)
    ]
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
