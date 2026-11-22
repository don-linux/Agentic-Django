"""Argon2 with its cost read from settings (DJANGO_ARGON2_* in the environment)."""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth.hashers import (
    Argon2PasswordHasher as DjangoArgon2PasswordHasher,
)


class Argon2PasswordHasher(DjangoArgon2PasswordHasher):
    """
    Django's Argon2 hasher with its cost read from settings on every call.

    Properties, so override_settings works in tests (Django uses class attributes,
    hence the type: ignore). See docs/deploy.md#capacity.
    """

    algorithm = "argon2"

    @property
    def time_cost(self) -> int:  # type: ignore[override]  # pyright: ignore[reportIncompatibleVariableOverride]
        return settings.ARGON2_TIME_COST

    @property
    def memory_cost(self) -> int:  # type: ignore[override]  # pyright: ignore[reportIncompatibleVariableOverride]
        return settings.ARGON2_MEMORY_COST_KIB

    @property
    def parallelism(self) -> int:  # type: ignore[override]  # pyright: ignore[reportIncompatibleVariableOverride]
        return settings.ARGON2_PARALLELISM
