from __future__ import annotations

from django.contrib.auth.hashers import check_password
from django.contrib.auth.hashers import get_hasher
from django.contrib.auth.hashers import make_password
from django.test import override_settings

from config.settings import base as base_settings
from core.hashers import Argon2PasswordHasher

PASSWORD = "Correct-H0rse-Battery"  # noqa: S105
# Small costs keep the tests fast.
CHEAP = {"ARGON2_TIME_COST": 1, "ARGON2_MEMORY_COST_KIB": 8, "ARGON2_PARALLELISM": 1}


def test_base_settings_use_the_configurable_hasher_with_django_defaults() -> None:
    assert base_settings.PASSWORD_HASHERS[0] == "core.hashers.Argon2PasswordHasher"
    assert base_settings.ARGON2_TIME_COST == 2  # noqa: PLR2004
    assert base_settings.ARGON2_MEMORY_COST_KIB == 102400  # noqa: PLR2004
    assert base_settings.ARGON2_PARALLELISM == 8  # noqa: PLR2004


@override_settings(**CHEAP)
def test_hasher_reads_the_cost_from_settings() -> None:
    hasher = Argon2PasswordHasher()

    params = hasher.decode(hasher.encode(PASSWORD, hasher.salt()))["params"]

    assert (params.time_cost, params.memory_cost, params.parallelism) == (1, 8, 1)


@override_settings(
    PASSWORD_HASHERS=["core.hashers.Argon2PasswordHasher"],
    **CHEAP,
)
def test_old_hashes_verify_and_are_upgraded_after_a_cost_change() -> None:
    encoded = make_password(PASSWORD)

    with override_settings(ARGON2_MEMORY_COST_KIB=16):
        assert check_password(PASSWORD, encoded)
        assert get_hasher("argon2").must_update(encoded)

    assert not get_hasher("argon2").must_update(encoded)
