"""The users router is mounted once on the project API, also after a reload."""

from __future__ import annotations

import importlib
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.urls import clear_url_caches

import config.urls
from core.api.api import api

if TYPE_CHECKING:
    from django.test import Client

USERS_PATHS = ["/api/v1/users/", "/api/v1/users/me/", "/api/v1/users/{id}/"]


def _published_paths() -> list[str]:
    return sorted(api.get_openapi_schema()["paths"])


def test_the_users_router_is_mounted_once() -> None:
    assert _published_paths() == USERS_PATHS


@pytest.mark.django_db
def test_reloading_the_urlconf_does_not_mount_the_router_again(client: Client) -> None:
    # The DEBUG root redirect tests reload config.urls; Ninja refuses add_router()
    # after api.urls was built, so mounting there would raise ConfigError.
    try:
        importlib.reload(config.urls)
    finally:
        clear_url_caches()

    assert _published_paths() == USERS_PATHS
    response = client.get("/api/v1/users/me/")
    assert response.status_code == HTTPStatus.UNAUTHORIZED
