from __future__ import annotations

import subprocess
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.settings.guards import assert_production_email
from config.settings.guards import assert_production_hosts
from config.settings.guards import assert_production_hsts
from config.settings.guards import assert_production_secrets
from config.settings.guards import assert_production_valkey
from core.tests.guard_values import VALID_EMAIL
from core.tests.guard_values import VALID_SECRETS
from core.tests.guard_values import _smtp
from core.tests.production_settings import load_production_settings


def test_case_variants_of_placeholders_are_rejected() -> None:
    with pytest.raises(ImproperlyConfigured, match="DJANGO_SECRET_KEY"):
        assert_production_secrets(
            **{**VALID_SECRETS, "secret_key": ("CHANGE-ME-" * 6)[:55]},
        )
    with pytest.raises(ImproperlyConfigured, match="DJANGO_ADMIN_URL"):
        assert_production_secrets(**{**VALID_SECRETS, "admin_url": "Change-Me/"})
    with pytest.raises(ImproperlyConfigured, match="POSTGRES_PASSWORD"):
        assert_production_secrets(**{**VALID_SECRETS, "db_password": "CHANGE-ME"})
    with pytest.raises(ImproperlyConfigured, match="POSTGRES_PASSWORD"):
        assert_production_secrets(**{**VALID_SECRETS, "db_password": "changeme"})
    with pytest.raises(ImproperlyConfigured, match="POSTGRES_PASSWORD"):
        assert_production_secrets(**{**VALID_SECRETS, "db_password": ""})
    assert_production_secrets(**{**VALID_SECRETS, "db_password": None})


@pytest.mark.parametrize(
    "admin_url", ["api/", "api/v1/", "_allauth/", "static/", "Media/ops/", "health/"]
)
def test_an_admin_url_under_an_app_route_is_rejected(admin_url: str) -> None:
    with pytest.raises(ImproperlyConfigured, match="DJANGO_ADMIN_URL"):
        assert_production_secrets(**{**VALID_SECRETS, "admin_url": admin_url})


@pytest.mark.parametrize("admin_url", ["staff-7f3k/", "apiary/", "ops/api/"])
def test_an_admin_url_beside_the_app_routes_is_accepted(admin_url: str) -> None:
    assert_production_secrets(**{**VALID_SECRETS, "admin_url": admin_url})


def test_example_and_empty_and_star_hosts_are_rejected() -> None:
    for hosts in (
        [],
        ["example.com"],
        ["example.org"],
        ["api.example.net"],
        ["localhost"],
        ["*.tests.invalid"],
    ):
        with pytest.raises(ImproperlyConfigured, match="DJANGO_ALLOWED_HOSTS"):
            assert_production_hosts(allowed_hosts=hosts)
    assert_production_hosts(allowed_hosts=[".tests.invalid"])


def test_empty_from_and_example_org_and_http_frontend_are_rejected() -> None:
    with pytest.raises(ImproperlyConfigured, match="DJANGO_DEFAULT_FROM_EMAIL"):
        assert_production_email(**{**VALID_EMAIL, "default_from_email": ""})
    with pytest.raises(ImproperlyConfigured, match="DJANGO_DEFAULT_FROM_EMAIL"):
        assert_production_email(
            **{**VALID_EMAIL, "default_from_email": "Ops <noreply@example.org>"},
        )
    with pytest.raises(ImproperlyConfigured, match="FRONTEND_URL"):
        assert_production_email(
            **{**VALID_EMAIL, "frontend_url": "http://app.tests.invalid"},
        )
    with pytest.raises(ImproperlyConfigured, match="DJANGO_MAILER_HOST"):
        assert_production_email(
            **{**VALID_EMAIL, "mailers": _smtp(host="smtp.example.org")}
        )


def test_csrf_origins_with_slash_http_or_example_are_rejected() -> None:
    hosts = ["api.tests.invalid"]
    assert_production_hosts(
        allowed_hosts=hosts,
        csrf_trusted_origins=["https://app.tests.invalid"],
    )
    assert_production_hosts(allowed_hosts=hosts, csrf_trusted_origins=[])
    for origin in (
        "https://app.tests.invalid/",
        "http://app.tests.invalid",
        "https://example.com",
        "https://app.example.org",
    ):
        with pytest.raises(ImproperlyConfigured, match="DJANGO_CSRF_TRUSTED_ORIGINS"):
            assert_production_hosts(allowed_hosts=hosts, csrf_trusted_origins=[origin])


def test_cors_origins_with_slash_http_example_or_localhost_are_rejected() -> None:
    hosts = ["api.tests.invalid"]
    assert_production_hosts(
        allowed_hosts=hosts,
        cors_allowed_origins=["https://app.tests.invalid"],
    )
    assert_production_hosts(allowed_hosts=hosts, cors_allowed_origins=[])
    for origin in (
        "https://app.tests.invalid/",
        "http://app.tests.invalid",
        "https://app.example.com",
        "http://localhost:3000",
    ):
        with pytest.raises(ImproperlyConfigured, match="CORS_ALLOWED_ORIGINS"):
            assert_production_hosts(allowed_hosts=hosts, cors_allowed_origins=[origin])


def test_hsts_preload_or_zero_max_age_is_rejected() -> None:
    assert_production_hsts(seconds=60, include_subdomains=False, preload=False)
    assert_production_hsts(seconds=31_536_000, include_subdomains=True, preload=True)
    with pytest.raises(ImproperlyConfigured, match="DJANGO_SECURE_HSTS_SECONDS"):
        assert_production_hsts(seconds=0, include_subdomains=False, preload=False)
    with pytest.raises(ImproperlyConfigured, match="DJANGO_SECURE_HSTS"):
        assert_production_hsts(seconds=60, include_subdomains=True, preload=False)
    with pytest.raises(ImproperlyConfigured, match="DJANGO_SECURE_HSTS"):
        assert_production_hsts(seconds=60, include_subdomains=False, preload=True)


def test_valkey_password_case_variant_is_rejected() -> None:
    with pytest.raises(ImproperlyConfigured, match="VALKEY_PASSWORD"):
        assert_production_valkey(
            url="redis://valkey.tests.invalid:6379",
            password="CHANGE-ME-CHANGE-ME-CHANGE-ME-XX",  # noqa: S106
            cache_db=0,
            broker_db=1,
        )


@pytest.mark.parametrize(
    ("env", "variable"),
    [
        ({"DJANGO_DEFAULT_FROM_EMAIL": ""}, "DJANGO_DEFAULT_FROM_EMAIL"),
        ({"DJANGO_ALLOWED_HOSTS": "example.com"}, "DJANGO_ALLOWED_HOSTS"),
        ({"DJANGO_ALLOWED_HOSTS": ""}, "DJANGO_ALLOWED_HOSTS"),
        ({"DJANGO_ALLOWED_HOSTS": "*.tests.invalid"}, "DJANGO_ALLOWED_HOSTS"),
        ({"DJANGO_SECRET_KEY": ("CHANGE-ME-" * 6)[:55]}, "DJANGO_SECRET_KEY"),
        ({"DJANGO_ADMIN_URL": "Change-Me/"}, "DJANGO_ADMIN_URL"),
        ({"DJANGO_ADMIN_URL": "api/v1/"}, "DJANGO_ADMIN_URL"),
        ({"POSTGRES_PASSWORD": "", "DATABASE_URL": None}, "POSTGRES_PASSWORD"),
        ({"FRONTEND_URL": "http://app.tests.invalid"}, "FRONTEND_URL"),
        (
            {"DJANGO_CSRF_TRUSTED_ORIGINS": "https://app.tests.invalid/"},
            "DJANGO_CSRF_TRUSTED_ORIGINS",
        ),
        (
            {"DJANGO_CSRF_TRUSTED_ORIGINS": "http://app.tests.invalid"},
            "DJANGO_CSRF_TRUSTED_ORIGINS",
        ),
        (
            {"DJANGO_CSRF_TRUSTED_ORIGINS": "https://example.com"},
            "DJANGO_CSRF_TRUSTED_ORIGINS",
        ),
        (
            {"CORS_ALLOWED_ORIGINS": "https://app.example.com"},
            "CORS_ALLOWED_ORIGINS",
        ),
        (
            {"CORS_ALLOWED_ORIGINS": "http://localhost:3000"},
            "CORS_ALLOWED_ORIGINS",
        ),
        ({"DJANGO_SECURE_HSTS_SECONDS": "0"}, "DJANGO_SECURE_HSTS_SECONDS"),
        ({"DJANGO_SECURE_HSTS_PRELOAD": "true"}, "DJANGO_SECURE_HSTS_PRELOAD"),
        (
            {"DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS": "true"},
            "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS",
        ),
    ],
)
def test_production_module_refuses_operator_mistakes(
    env: dict[str, Any],
    variable: str,
) -> None:
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        load_production_settings("SECRET_KEY", env=env)
    assert "ImproperlyConfigured" in excinfo.value.stderr
    assert variable in excinfo.value.stderr
    for value in env.values():
        if isinstance(value, str) and ("CHANGE-ME" in value or "Change-Me" in value):
            assert value not in excinfo.value.stderr
