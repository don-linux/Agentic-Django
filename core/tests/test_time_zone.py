from __future__ import annotations

import pytest

from core.tests.production_settings import load_settings


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [(None, "UTC"), ("America/Mexico_City", "America/Mexico_City")],
)
def test_time_zone_comes_from_env_and_defaults_to_utc(
    env_value: str | None,
    expected: str,
) -> None:
    settings = load_settings(
        "config.settings.base",
        "TIME_ZONE",
        "USE_TZ",
        env={"DJANGO_TIME_ZONE": env_value},
    )

    assert settings["TIME_ZONE"] == expected
    assert settings["USE_TZ"] is True
