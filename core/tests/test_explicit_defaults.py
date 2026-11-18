"""Defaults the template states explicitly; changing one is a deliberate decision."""

from __future__ import annotations

from django.conf import settings

from config import celery_app

TWO_AND_A_HALF_MEGABYTES = 2_621_440
FILES_PER_REQUEST = 100


def test_request_bodies_are_capped_at_two_and_a_half_megabytes() -> None:
    assert settings.DATA_UPLOAD_MAX_MEMORY_SIZE == TWO_AND_A_HALF_MEGABYTES


def test_uploaded_files_spool_to_disk_above_two_and_a_half_megabytes() -> None:
    assert settings.FILE_UPLOAD_MAX_MEMORY_SIZE == TWO_AND_A_HALF_MEGABYTES


def test_a_request_carries_at_most_a_hundred_files() -> None:
    assert settings.DATA_UPLOAD_MAX_NUMBER_FILES == FILES_PER_REQUEST


def test_celery_results_would_be_json() -> None:
    assert celery_app.conf.result_serializer == "json"
