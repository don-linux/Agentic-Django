# SLF001: Model._meta is Django's documented Meta API. DJ008: test-only models.
# ruff: noqa: SLF001, DJ008
"""
on_delete policy for foreign keys in project apps.

FKs default to PROTECT; CASCADE only for owned children listed in CASCADE_ALLOWED.
See docs/ARCHITECTURE.md#layers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.apps import apps
from django.conf import settings
from django.db import models
from django.test.utils import isolate_apps

if TYPE_CHECKING:
    from collections.abc import Iterable

# "<app_label>.<Model>.<field>": why the child row belongs to its parent.
CASCADE_ALLOWED: dict[str, str] = {}


def cascade_violations(
    candidates: Iterable[type[models.Model]],
    allowed: dict[str, str],
) -> list[str]:
    """Return "<app_label>.<Model>.<field>" for every FK that cascades without leave."""
    violations = []
    for model in candidates:
        for field in model._meta.get_fields():
            if not isinstance(field, models.ForeignKey):  # OneToOneField included
                continue
            path = f"{model._meta.app_label}.{model.__name__}.{field.name}"
            if field.remote_field.on_delete is models.CASCADE and path not in allowed:
                violations.append(path)
    return violations


def _project_models() -> list[type[models.Model]]:
    local_apps = set(settings.LOCAL_APPS)
    return [
        model
        for model in apps.get_models()
        if model._meta.app_config.name in local_apps
    ]


def test_project_foreign_keys_follow_the_on_delete_policy() -> None:
    violations = cascade_violations(_project_models(), CASCADE_ALLOWED)

    assert violations == [], (
        "These foreign keys use on_delete=CASCADE. Use PROTECT (or SET_NULL for an "
        "optional link), or add them to CASCADE_ALLOWED with the reason: "
        f"{violations}"
    )


def test_every_allowed_cascade_still_exists() -> None:
    existing = {
        f"{model._meta.app_label}.{model.__name__}.{field.name}"
        for model in _project_models()
        for field in model._meta.get_fields()
        if isinstance(field, models.ForeignKey)
    }

    assert set(CASCADE_ALLOWED) <= existing


@isolate_apps("core")
def test_checker_flags_cascade_and_accepts_protect_and_allowed_entries() -> None:
    class Parent(models.Model):
        pass

    class Child(models.Model):
        protected = models.ForeignKey(
            Parent, on_delete=models.PROTECT, related_name="+"
        )
        optional = models.ForeignKey(
            Parent, on_delete=models.SET_NULL, null=True, related_name="+"
        )
        cascading = models.ForeignKey(
            Parent, on_delete=models.CASCADE, related_name="+"
        )
        owned = models.OneToOneField(Parent, on_delete=models.CASCADE, related_name="+")

    candidates = [Parent, Child]

    assert cascade_violations(candidates, {}) == [
        "core.Child.cascading",
        "core.Child.owned",
    ]
    assert cascade_violations(
        candidates, {"core.Child.owned": "owned by its parent"}
    ) == [
        "core.Child.cascading",
    ]
