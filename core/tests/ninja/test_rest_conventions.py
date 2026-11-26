"""Every router is mounted once on the project API, also after a reload."""

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

# The users app's paths: a project's own routers add theirs next to them.
USERS_PATHS = {"/api/v1/users/", "/api/v1/users/me/", "/api/v1/users/{id}/"}


def _operation_ids() -> list[str]:
    paths = api.get_openapi_schema()["paths"]
    return [op["operationId"] for item in paths.values() for op in item.values()]


def _published_paths() -> set[str]:
    return set(api.get_openapi_schema()["paths"])


def test_every_router_is_mounted_once() -> None:
    # A router mounted twice repeats its operation ids (and Ninja suffixes
    # its paths); the users routes are there whatever else a project adds.
    operation_ids = _operation_ids()
    assert len(operation_ids) == len(set(operation_ids)), operation_ids
    assert _published_paths() >= USERS_PATHS


@pytest.mark.django_db
def test_reloading_the_urlconf_does_not_mount_the_router_again(client: Client) -> None:
    # The DEBUG root redirect tests reload config.urls; Ninja refuses add_router()
    # after api.urls was built, so mounting there would raise ConfigError.
    try:
        importlib.reload(config.urls)
    finally:
        clear_url_caches()

    operation_ids = _operation_ids()
    assert len(operation_ids) == len(set(operation_ids)), operation_ids
    assert _published_paths() >= USERS_PATHS
    response = client.get("/api/v1/users/me/")
    assert response.status_code == HTTPStatus.UNAUTHORIZED
