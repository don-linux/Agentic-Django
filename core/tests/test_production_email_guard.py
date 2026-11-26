from __future__ import annotations

import subprocess
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.settings.guards import assert_production_email
from core.tests.guard_values import VALID_EMAIL as VALID
from core.tests.guard_values import _smtp
from core.tests.production_settings import load_production_settings


def test_real_values_pass() -> None:
    assert_production_email(**VALID)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "::1"])
def test_a_loopback_mail_host_is_refused(host: str) -> None:
    # In the container, loopback is the container itself: nothing answers SMTP.
    with pytest.raises(ImproperlyConfigured, match="DJANGO_MAILER_HOST"):
        assert_production_email(**{**VALID, "mailers": _smtp(host=host)})


def test_a_relay_on_the_host_and_anymail_pass() -> None:
    assert_production_email(**{**VALID, "mailers": _smtp(host="host.docker.internal")})
    anymail = {"default": {"BACKEND": "anymail.backends.postmark.EmailBackend"}}
    assert_production_email(**{**VALID, "mailers": anymail})


def test_notexample_com_is_not_treated_as_a_placeholder() -> None:
    # Only example.com and its subdomains are placeholders, not a name that
    # merely ends with the same letters.
    assert_production_email(
        **{
            **VALID,
            "mailers": _smtp(host="smtp.notexample.com"),
            "default_from_email": "Ops <noreply@notexample.com>",
            "frontend_url": "https://app.example.com.mx",
        },
    )


@pytest.mark.parametrize(
    ("overrides", "variable"),
    [
        ({"mailers": _smtp(host="")}, "DJANGO_MAILER_HOST"),
        ({"mailers": _smtp(host="   ")}, "DJANGO_MAILER_HOST"),
        ({"mailers": _smtp(host="smtp.example.com")}, "DJANGO_MAILER_HOST"),
        ({"mailers": _smtp(host="smtp.example.org")}, "DJANGO_MAILER_HOST"),
        ({"mailers": _smtp(host="smtp.example.net")}, "DJANGO_MAILER_HOST"),
        ({"mailers": _smtp(use_ssl=True)}, "DJANGO_MAILER_USE_TLS"),
        ({"mailers": {}}, "MAILERS"),
        (
            {
                "mailers": {
                    "default": {
                        "BACKEND": "django.core.mail.backends.console.EmailBackend"
                    }
                }
            },
            "MAILERS",
        ),
        (
            {
                "mailers": {
                    "default": {
                        "BACKEND": "django.core.mail.backends.locmem.EmailBackend"
                    }
                }
            },
            "MAILERS",
        ),
        (
            {"default_from_email": "Project <noreply@example.com>"},
            "DJANGO_DEFAULT_FROM_EMAIL",
        ),
        (
            {"default_from_email": "Project <noreply@example.org>"},
            "DJANGO_DEFAULT_FROM_EMAIL",
        ),
        (
            {"default_from_email": "Project <noreply@example.net>"},
            "DJANGO_DEFAULT_FROM_EMAIL",
        ),
        ({"default_from_email": ""}, "DJANGO_DEFAULT_FROM_EMAIL"),
        ({"default_from_email": "   "}, "DJANGO_DEFAULT_FROM_EMAIL"),
        ({"default_from_email": "webmaster@localhost"}, "DJANGO_DEFAULT_FROM_EMAIL"),
        ({"frontend_url": "https://app.example.com"}, "FRONTEND_URL"),
        ({"frontend_url": "https://app.example.org"}, "FRONTEND_URL"),
        ({"frontend_url": "http://localhost:3000"}, "FRONTEND_URL"),
        ({"frontend_url": ""}, "FRONTEND_URL"),
    ],
)
def test_example_or_broken_values_are_rejected(
    overrides: dict[str, Any],
    variable: str,
) -> None:
    with pytest.raises(ImproperlyConfigured, match=variable):
        assert_production_email(**{**VALID, **overrides})


def test_production_module_calls_the_email_guard() -> None:
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        load_production_settings("MAILERS", env={"FRONTEND_URL": None})

    assert "FRONTEND_URL" in excinfo.value.stderr


def test_production_rejects_empty_from_email() -> None:
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        load_production_settings(
            "DEFAULT_FROM_EMAIL",
            env={"DJANGO_DEFAULT_FROM_EMAIL": ""},
        )
    assert "DJANGO_DEFAULT_FROM_EMAIL" in excinfo.value.stderr


def test_production_rejects_example_org_from_and_frontend() -> None:
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        load_production_settings(
            "DEFAULT_FROM_EMAIL",
            env={
                "DJANGO_DEFAULT_FROM_EMAIL": "Project <noreply@example.org>",
                "DJANGO_MAILER_HOST": "smtp.example.org",
                "FRONTEND_URL": "https://app.example.org",
            },
        )
    stderr = excinfo.value.stderr
    assert "DJANGO_DEFAULT_FROM_EMAIL" in stderr
    assert "FRONTEND_URL" in stderr
    assert "DJANGO_MAILER_HOST" in stderr
