from __future__ import annotations

import datetime as dt
from datetime import timedelta
from typing import Any

import pytest
from django.apps.registry import Apps
from django.db import connection
from django.db import models
from django.utils import timezone
from django_stubs_ext.db.models import TypedModelMeta

from core.models import BaseModel


def _build_sample_model() -> Any:
    isolated_apps = Apps(["core"])

    class SampleItem(BaseModel):
        title = models.CharField(max_length=100)

        class Meta(BaseModel.Meta, TypedModelMeta):
            app_label = "core"
            apps = isolated_apps

    return SampleItem


@pytest.mark.django_db
def test_save_with_update_fields_automatically_updates_updated_at() -> None:
    sample_model = _build_sample_model()
    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(sample_model)

    item = sample_model.objects.create(title="Initial")
    past = timezone.now() - timedelta(hours=1)
    sample_model.objects.filter(pk=item.pk).update(updated_at=past)
    item.refresh_from_db()
    assert item.updated_at == past

    item.title = "Renamed"
    item.save(update_fields=["title"])
    item.refresh_from_db()

    assert item.title == "Renamed"
    assert item.updated_at > past


@pytest.mark.django_db
def test_save_with_empty_update_fields_is_noop() -> None:
    sample_model = _build_sample_model()
    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(sample_model)

    item = sample_model.objects.create(title="Initial")
    past = timezone.now() - timedelta(hours=1)
    sample_model.objects.filter(pk=item.pk).update(updated_at=past)
    item.refresh_from_db()

    item.title = "Ignored"
    item.save(update_fields=[])
    # Nothing was written, so updated_at must still be a value, not Now().
    assert item.updated_at == past
    item.refresh_from_db()

    assert item.title == "Initial"
    assert item.updated_at == past


def _create_sample_table() -> Any:
    sample_model = _build_sample_model()
    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(sample_model)
    return sample_model


@pytest.mark.django_db
def test_timestamps_come_from_the_database_on_create() -> None:
    sample_model = _create_sample_table()

    item = sample_model.objects.create(title="Initial")

    # Returned by the INSERT (RETURNING), without refresh_from_db().
    assert isinstance(item.created_at, dt.datetime)
    assert item.created_at == item.updated_at


@pytest.mark.django_db
def test_save_moves_updated_at_past_created_at() -> None:
    sample_model = _create_sample_table()
    item = sample_model.objects.create(title="Initial")

    item.title = "Renamed"
    item.save()

    assert isinstance(item.updated_at, dt.datetime)
    assert item.updated_at > item.created_at


@pytest.mark.django_db
def test_bulk_update_bumps_updated_at_unless_it_is_given() -> None:
    sample_model = _create_sample_table()
    item = sample_model.objects.create(title="Initial")
    past = timezone.now() - timedelta(hours=1)

    sample_model.objects.filter(pk=item.pk).update(updated_at=past)
    item.refresh_from_db()
    assert item.updated_at == past

    sample_model.objects.filter(pk=item.pk).update(title="Bulk")
    item.refresh_from_db()
    assert item.title == "Bulk"
    assert item.updated_at > past


@pytest.mark.django_db
def test_full_clean_accepts_an_unsaved_instance() -> None:
    sample_model = _create_sample_table()

    # Services call full_clean() before save(), while both timestamps are still
    # database defaults.
    sample_model(title="Draft").full_clean()
