"""Responses are never cached on the server: the parts ruff cannot check alone."""

from __future__ import annotations

import tomllib
from pathlib import Path

from django.conf import settings

from core.tests.production_settings import load_production_settings

BASE_DIR = Path(__file__).resolve().parents[2]
PER_SITE_CACHE = "django.middleware.cache."


def test_ruff_bans_the_response_cache_apis() -> None:
    pyproject = tomllib.loads((BASE_DIR / "pyproject.toml").read_text())
    lint = pyproject["tool"]["ruff"]["lint"]

    assert "TID" in lint["select"]
    assert set(lint["flake8-tidy-imports"]["banned-api"]) >= {
        "django.views.decorators.cache.cache_page",
        "django.middleware.cache",
    }


def test_no_per_site_cache_middleware() -> None:
    # ruff only sees imports, and MIDDLEWARE names its classes as strings.
    assert not [name for name in settings.MIDDLEWARE if PER_SITE_CACHE in name]


def test_production_has_no_per_site_cache_middleware() -> None:
    middleware = load_production_settings("MIDDLEWARE")["MIDDLEWARE"]

    assert not [name for name in middleware if PER_SITE_CACHE in name]
