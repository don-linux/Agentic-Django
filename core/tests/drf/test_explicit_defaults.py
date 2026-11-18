"""DRF defaults the template states explicitly; changing one is deliberate."""

from __future__ import annotations

from rest_framework.settings import api_settings


def test_decimals_render_as_strings() -> None:
    assert api_settings.COERCE_DECIMAL_TO_STRING is True
