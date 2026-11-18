"""Passwords longer than 256 characters are refused wherever one is set."""

from __future__ import annotations

from http import HTTPStatus

import pytest
from django.contrib.auth.password_validation import password_validators_help_texts
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.test import Client

from core.password_validation import MaximumLengthValidator

SIGNUP_URL = "/_allauth/app/v1/auth/signup"
STRONG = "Correct-H0rse-Battery-"


def _password(length: int) -> str:
    return (STRONG * (length // len(STRONG) + 1))[:length]


def test_256_characters_are_accepted() -> None:
    MaximumLengthValidator().validate(_password(256))


def test_257_characters_are_refused() -> None:
    with pytest.raises(ValidationError) as excinfo:
        MaximumLengthValidator().validate(_password(257))

    assert excinfo.value.code == "password_too_long"
    assert "256" in excinfo.value.messages[0]


def test_the_validator_is_configured() -> None:
    with pytest.raises(ValidationError):
        validate_password(_password(257))
    assert (
        "Your password must contain at most 256 characters."
        in password_validators_help_texts()
    )


@pytest.mark.django_db
def test_signup_refuses_a_too_long_password() -> None:
    response = Client().post(
        SIGNUP_URL,
        {"email": "long-password@example.com", "password": _password(257)},
        content_type="application/json",
    )

    assert response.status_code == HTTPStatus.BAD_REQUEST
    codes = {error["code"] for error in response.json()["errors"]}
    assert "password_too_long" in codes
