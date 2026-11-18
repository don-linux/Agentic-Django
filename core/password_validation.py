"""Password validators that Django does not ship."""

from __future__ import annotations

from django.core.exceptions import ValidationError

DEFAULT_MAX_LENGTH = 256


class MaximumLengthValidator:
    """
    Refuse passwords longer than `max_length` characters.

    A password manager never needs more, and a megabyte "password" is only a way
    to make every request that hashes it heavier. The cost of each Argon2 hash
    comes from its memory setting, not from this cap (docs/security.md#passwords).
    """

    message = (
        "This password is too long. It must contain at most %(max_length)d characters."
    )
    code = "password_too_long"

    def __init__(self, max_length: int = DEFAULT_MAX_LENGTH) -> None:
        self.max_length = max_length

    def validate(self, password: str, user: object = None) -> None:
        if len(password) > self.max_length:
            raise ValidationError(
                self.message,
                code=self.code,
                params={"max_length": self.max_length},
            )

    def get_help_text(self) -> str:
        return f"Your password must contain at most {self.max_length} characters."
