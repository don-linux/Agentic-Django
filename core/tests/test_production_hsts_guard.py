from __future__ import annotations

import subprocess

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.settings.guards import HSTS_PRELOAD_MIN_SECONDS
from config.settings.guards import assert_production_hsts
from config.settings.guards import silenced_hsts_system_checks
from core.tests.production_settings import load_production_settings
from core.tests.production_settings import run_python

YEAR = HSTS_PRELOAD_MIN_SECONDS
DEFAULT_SECONDS = 60


def test_defaults_silence_both_flag_checks() -> None:
    assert silenced_hsts_system_checks(
        include_subdomains=False,
        preload=False,
    ) == ["security.W005", "security.W021"]


def test_one_year_with_flags_off_still_silences_both() -> None:
    # The silence list does not depend on the duration.
    assert silenced_hsts_system_checks(
        include_subdomains=False,
        preload=False,
    ) == ["security.W005", "security.W021"]


def test_include_subdomains_leaves_only_preload_silenced() -> None:
    assert silenced_hsts_system_checks(
        include_subdomains=True,
        preload=False,
    ) == ["security.W021"]


def test_preload_leaves_only_include_subdomains_silenced() -> None:
    assert silenced_hsts_system_checks(
        include_subdomains=False,
        preload=True,
    ) == ["security.W005"]


def test_both_flags_on_silences_nothing() -> None:
    assert silenced_hsts_system_checks(include_subdomains=True, preload=True) == []


def test_preload_without_one_year_is_rejected() -> None:
    with pytest.raises(ImproperlyConfigured, match="DJANGO_SECURE_HSTS_SECONDS"):
        assert_production_hsts(
            seconds=DEFAULT_SECONDS, include_subdomains=True, preload=True
        )


def test_preload_without_include_subdomains_is_rejected() -> None:
    with pytest.raises(
        ImproperlyConfigured,
        match="DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS",
    ):
        assert_production_hsts(seconds=YEAR, include_subdomains=False, preload=True)


def test_valid_preload_passes() -> None:
    assert_production_hsts(seconds=YEAR, include_subdomains=True, preload=True)
    assert_production_hsts(
        seconds=DEFAULT_SECONDS, include_subdomains=False, preload=False
    )
    assert_production_hsts(seconds=YEAR, include_subdomains=False, preload=False)


def test_production_defaults_are_sixty_seconds_with_flags_off() -> None:
    settings = load_production_settings(
        "SECURE_HSTS_SECONDS",
        "SECURE_HSTS_INCLUDE_SUBDOMAINS",
        "SECURE_HSTS_PRELOAD",
        "SILENCED_SYSTEM_CHECKS",
        "SESSION_COOKIE_SECURE",
        "CSRF_COOKIE_SECURE",
        "SESSION_COOKIE_NAME",
        "CSRF_COOKIE_NAME",
        "SECURE_SSL_REDIRECT",
        "DEBUG",
    )
    assert settings["SECURE_HSTS_SECONDS"] == DEFAULT_SECONDS
    assert settings["SECURE_HSTS_INCLUDE_SUBDOMAINS"] is False
    assert settings["SECURE_HSTS_PRELOAD"] is False
    assert settings["SILENCED_SYSTEM_CHECKS"] == ["security.W005", "security.W021"]
    assert settings["SESSION_COOKIE_SECURE"] is True
    assert settings["CSRF_COOKIE_SECURE"] is True
    assert settings["SESSION_COOKIE_NAME"] == "__Secure-sessionid"
    assert settings["CSRF_COOKIE_NAME"] == "__Secure-csrftoken"
    assert settings["SECURE_SSL_REDIRECT"] is True
    assert settings["DEBUG"] is False


def test_production_one_year_keeps_flag_checks_silenced() -> None:
    settings = load_production_settings(
        "SECURE_HSTS_SECONDS",
        "SILENCED_SYSTEM_CHECKS",
        env={"DJANGO_SECURE_HSTS_SECONDS": str(YEAR)},
    )
    assert settings["SECURE_HSTS_SECONDS"] == YEAR
    assert settings["SILENCED_SYSTEM_CHECKS"] == ["security.W005", "security.W021"]


def test_production_rejects_preload_at_sixty_seconds() -> None:
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        load_production_settings(
            "SECURE_HSTS_PRELOAD",
            env={"DJANGO_SECURE_HSTS_PRELOAD": "true"},
        )
    assert "DJANGO_SECURE_HSTS_PRELOAD" in excinfo.value.stderr
    assert "DJANGO_SECURE_HSTS_SECONDS" in excinfo.value.stderr


def _check_deploy(
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    child = {"DJANGO_SETTINGS_MODULE": "config.settings.production"}
    if env:
        child.update(env)
    return run_python(
        "from django.core.management import execute_from_command_line\n"
        "execute_from_command_line(['manage.py', 'check', '--deploy', "
        "'--fail-level', 'WARNING'])\n",
        env=child,
    )


def test_check_deploy_passes_with_default_hsts() -> None:
    result = _check_deploy()
    combined = (result.stdout + result.stderr).lower()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no issues" in combined or "silenced" in combined


def test_check_deploy_passes_with_one_year_and_flags_off() -> None:
    result = _check_deploy(env={"DJANGO_SECURE_HSTS_SECONDS": str(YEAR)})
    assert result.returncode == 0, result.stdout + result.stderr


def test_check_deploy_passes_in_ci_like_env() -> None:
    result = _check_deploy(
        env={
            "DJANGO_ALLOWED_HOSTS": "api.ci.invalid",
            "DJANGO_MAILER_HOST": "smtp.ci.invalid",
            "DJANGO_DEFAULT_FROM_EMAIL": "CI <noreply@ci.invalid>",
            "FRONTEND_URL": "https://app.ci.invalid",
            "POSTGRES_PASSWORD": "ci",
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
