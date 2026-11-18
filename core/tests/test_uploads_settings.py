from __future__ import annotations

import json
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.conf import settings
from django.test import Client

from core.tests.production_settings import load_settings

if TYPE_CHECKING:
    from apps.users.models import User

# rw-r--r-- for files, rwxr-xr-x for directories: readable by the host nginx user.
FILE_MODE = 0o644
DIRECTORY_MODE = 0o755


def test_upload_permissions_do_not_depend_on_the_umask() -> None:
    assert settings.FILE_UPLOAD_PERMISSIONS == FILE_MODE
    assert settings.FILE_UPLOAD_DIRECTORY_PERMISSIONS == DIRECTORY_MODE


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [(None, "/media/"), ("https://media.example.org/", "https://media.example.org/")],
)
def test_media_url_comes_from_env(env_value: str | None, expected: str) -> None:
    base = load_settings(
        "config.settings.base",
        "MEDIA_URL",
        env={"DJANGO_MEDIA_URL": env_value},
    )

    assert base["MEDIA_URL"] == expected


@pytest.mark.django_db
def test_json_body_over_the_memory_limit_is_a_json_400(user: User) -> None:
    client = Client(raise_request_exception=False)
    client.force_login(user)
    body = json.dumps({"name": "x" * (settings.DATA_UPLOAD_MAX_MEMORY_SIZE + 1)})

    response = client.patch(
        f"/api/v1/users/{user.public_id}/",
        data=body,
        content_type="application/json",
    )

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["code"] == "bad_request"
