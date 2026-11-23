"""config/settings/env.py: how the settings read the environment."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.core.exceptions import ImproperlyConfigured
from pydantic import ValidationError

from config.settings.env import BaseEnv
from config.settings.env import LocalEnv
from config.settings.env import ProductionEnv

# Imported on purpose: TestEnv.__test__ = False keeps pytest from collecting it,
# which would otherwise fail this module (warnings are errors).
from config.settings.env import TestEnv
from config.settings.env import read_env
from config.settings.env import required
from config.settings.env import split_commas
from core.tests.production_settings import PRODUCTION_ENV

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic_settings import BaseSettings

# A value no message may print: it stands for a secret.
LEAK = "leak-c0ffee-SECRET"


def _validate(env_class: type[BaseSettings]) -> BaseSettings:
    # What read_env does before it rewrites the error: the required fields come
    # from the environment, not from arguments.
    return env_class()


@pytest.fixture
def production_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """The variables ProductionEnv requires, as the subprocess tests use them."""
    for name, value in PRODUCTION_ENV.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


@pytest.mark.parametrize(
    ("raw", "items"),
    [
        ("a,b", ["a", "b"]),
        ("a, b", ["a", "b"]),
        (" a ,b ", ["a", "b"]),
        ("a,,b", ["a", "b"]),
        ("a,", ["a"]),
        ("", []),
        (" ", []),
        (
            '"Ops" <ops@example.org>,dev@example.org',
            ['"Ops" <ops@example.org>', "dev@example.org"],
        ),
    ],
)
def test_a_list_is_comma_separated(
    monkeypatch: pytest.MonkeyPatch, raw: str, items: list[str]
) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", raw)

    origins = read_env(BaseEnv).CORS_ALLOWED_ORIGINS

    assert origins == items


def test_a_list_is_not_json() -> None:
    # pydantic-settings reads a list as JSON by default; a JSON list here is one
    # item, like any other text without commas.
    assert split_commas('["a"]') == ['["a"]']
    assert split_commas(["already", "a", "list"]) == ["already", "a", "list"]


@pytest.mark.parametrize(
    ("raw", "value"),
    [
        *[(word, True) for word in ("true", "True", "1", "yes", "on")],
        *[(word, False) for word in ("false", "False", "0", "no", "off")],
    ],
)
def test_a_boolean_takes_the_usual_words(
    monkeypatch: pytest.MonkeyPatch, raw: str, *, value: bool
) -> None:
    monkeypatch.setenv("DJANGO_ACCOUNT_ALLOW_REGISTRATION", raw)

    assert read_env(BaseEnv).DJANGO_ACCOUNT_ALLOW_REGISTRATION is value


@pytest.mark.parametrize("raw", ["ok", "treu", "", " true", LEAK])
def test_any_other_boolean_is_refused_by_name_without_its_value(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    # django-environ read every one of these as False, or "ok" as True, silently.
    monkeypatch.setenv("DJANGO_ACCOUNT_ALLOW_REGISTRATION", raw)

    with pytest.raises(ImproperlyConfigured) as excinfo:
        read_env(BaseEnv)

    message = str(excinfo.value)
    assert message.startswith("DJANGO_ACCOUNT_ALLOW_REGISTRATION: Input should be")
    assert LEAK not in message
    # Nothing chained: the pydantic error stays out of the traceback.
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True


def test_a_value_starting_with_a_dollar_sign_is_read_as_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # django-environ replaced "$NAME" with the variable NAME, so a secret that
    # starts with "$" was lost.
    monkeypatch.setenv("HOME", "/home/somebody")
    monkeypatch.setenv("DJANGO_EMAIL_SIGNATURE_NAME", "$HOME")
    monkeypatch.setenv("VALKEY_PASSWORD", "$ecret-value")

    env = read_env(BaseEnv)

    assert env.DJANGO_EMAIL_SIGNATURE_NAME == "$HOME"
    assert env.VALKEY_PASSWORD.get_secret_value() == "$ecret-value"


def test_a_variable_name_is_case_sensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DJANGO_TIME_ZONE", raising=False)
    monkeypatch.setenv("django_time_zone", "Europe/Madrid")

    assert read_env(BaseEnv).DJANGO_TIME_ZONE == "UTC"


def test_an_empty_optional_value_is_none(production_env: pytest.MonkeyPatch) -> None:
    production_env.setenv("DJANGO_AWS_S3_REGION_NAME", "")
    production_env.setenv("DJANGO_MAILER_PORT", "")
    production_env.setenv("DATABASE_URL", "")

    env = read_env(ProductionEnv)

    assert env.DJANGO_AWS_S3_REGION_NAME is None
    assert env.DJANGO_MAILER_PORT is None
    assert env.DATABASE_URL is None


def test_an_empty_value_is_a_value_everywhere_else(
    production_env: pytest.MonkeyPatch,
) -> None:
    # The production guards refuse an empty mail host or password themselves.
    production_env.setenv("DJANGO_MAILER_HOST", "")
    production_env.setenv("POSTGRES_PASSWORD", "")

    password = read_env(BaseEnv).POSTGRES_PASSWORD

    assert read_env(ProductionEnv).DJANGO_MAILER_HOST == ""
    assert password is not None
    assert password.get_secret_value() == ""


def test_every_missing_required_variable_is_named_at_once(
    production_env: pytest.MonkeyPatch,
) -> None:
    missing = ("DJANGO_SECRET_KEY", "DJANGO_ADMIN_URL", "VALKEY_URL", "VALKEY_PASSWORD")
    for name in missing:
        production_env.delenv(name)

    with pytest.raises(ImproperlyConfigured) as excinfo:
        read_env(ProductionEnv)

    for name in missing:
        assert f"Set the {name} environment variable." in str(excinfo.value)


def test_an_invalid_value_is_named_without_its_value(
    production_env: pytest.MonkeyPatch,
) -> None:
    production_env.setenv("DJANGO_SECURE_HSTS_SECONDS", LEAK)
    production_env.setenv("DJANGO_SECRET_KEY_FALLBACKS", f"ok,{LEAK}")

    with pytest.raises(ImproperlyConfigured) as excinfo:
        read_env(ProductionEnv)

    message = str(excinfo.value)
    assert "DJANGO_SECURE_HSTS_SECONDS: Input should be a valid integer" in message
    assert LEAK not in message


def test_a_validation_error_never_shows_the_input(
    production_env: pytest.MonkeyPatch,
) -> None:
    # Called directly, without read_env: hide_input_in_errors keeps a value out
    # of the pydantic message too.
    production_env.setenv("DJANGO_SECURE_HSTS_SECONDS", LEAK)

    with pytest.raises(ValidationError) as excinfo:
        _validate(ProductionEnv)

    assert LEAK not in str(excinfo.value)


def test_no_settings_class_reads_a_dotenv_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Only local.py loads the root .env, into the process environment
    # (_env_file.py); a .env next to a production process is never read.
    (tmp_path / ".env").write_text("DJANGO_TIME_ZONE=Europe/Madrid\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DJANGO_TIME_ZONE", raising=False)

    assert read_env(BaseEnv).DJANGO_TIME_ZONE == "UTC"
    for env_class in (BaseEnv, LocalEnv, TestEnv, ProductionEnv):
        assert env_class.model_config.get("env_file") is None
        assert env_class.model_config.get("secrets_dir") is None


def test_required_refuses_only_none() -> None:
    assert required("", "POSTGRES_DB") == ""
    assert required("db", "POSTGRES_DB") == "db"
    with pytest.raises(
        ImproperlyConfigured, match=r"^Set the POSTGRES_DB environment variable\.$"
    ):
        required(None, "POSTGRES_DB")


def test_a_variable_two_modules_read_keeps_each_module_rule() -> None:
    assert BaseEnv.model_fields["DJANGO_DEBUG"].default is False
    assert LocalEnv.model_fields["DJANGO_DEBUG"].default is True
    assert BaseEnv.model_fields["DJANGO_LOG_FORMAT"].default == "text"
    assert ProductionEnv.model_fields["DJANGO_LOG_FORMAT"].default == "json"
    assert not BaseEnv.model_fields["VALKEY_URL"].is_required()
    assert ProductionEnv.model_fields["VALKEY_URL"].is_required()
    assert ProductionEnv.model_fields["VALKEY_PASSWORD"].is_required()
