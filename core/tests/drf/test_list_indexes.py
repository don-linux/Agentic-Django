# SLF001: Model._meta is Django's documented Meta API. DJ008: test-only models.
# ruff: noqa: SLF001, DJ008
"""Every list endpoint orders by an indexed column (docs/ARCHITECTURE.md#indexes)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from django.db import models
from django.test.utils import isolate_apps
from rest_framework.viewsets import GenericViewSet

from apps.users.tests.factories import UserFactory
from config.api_router import router

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


def ordering_problems(viewset_class: type[Any], user: Any) -> list[str]:
    """Return "<viewset>.<field>" for each unindexed default or allowed ordering."""
    viewset = viewset_class()
    viewset.request = SimpleNamespace(user=user)
    viewset.action = "list"
    viewset.kwargs = {}
    viewset.format_kwarg = None
    queryset = viewset.get_queryset()
    model = queryset.model
    ordering = list(queryset.query.order_by) or list(model._meta.ordering)
    fields = [str(ordering[0])] if ordering else []
    fields += [
        str(field) for field in getattr(viewset_class, "ordering_fields", None) or []
    ]
    return [
        f"{viewset_class.__name__}.{field.lstrip('-')}"
        for field in fields
        if field != "__all__" and not _is_indexed(model, field.lstrip("-"))
    ]


@pytest.mark.django_db
def test_list_endpoints_order_by_indexed_columns() -> None:
    user = UserFactory()
    problems = []
    list_viewsets = [
        viewset for _, viewset, _ in router.registry if hasattr(viewset, "list")
    ]
    for viewset in list_viewsets:
        problems += ordering_problems(viewset, user)

    assert list_viewsets, "no list endpoints registered"
    assert problems == [], f"Unindexed list ordering {problems}; see {DOCS}"


@isolate_apps("core")
def test_ordering_check_flags_unindexed_columns() -> None:
    class Item(models.Model):
        title = models.CharField(max_length=50)
        rank = models.IntegerField()
        code = models.CharField(max_length=10, unique=True)

        class Meta:
            indexes = [models.Index(fields=["-rank", "-id"], name="core_item_rank_idx")]

    def viewset_for(
        order_by: str, ordering_fields: list[str]
    ) -> type[GenericViewSet[Any]]:
        def get_queryset(self: GenericViewSet[Any]) -> models.QuerySet[Item]:
            return Item._default_manager.order_by(order_by)

        return type(
            "ItemViewSet",
            (GenericViewSet,),
            {"get_queryset": get_queryset, "ordering_fields": ordering_fields},
        )

    assert ordering_problems(viewset_for("-id", ["rank", "code"]), None) == []
    assert ordering_problems(viewset_for("title", ["rank"]), None) == [
        "ItemViewSet.title"
    ]
    assert ordering_problems(viewset_for("id", ["title"]), None) == [
        "ItemViewSet.title"
    ]
