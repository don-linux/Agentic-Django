"""
The only DRF-aware fixtures that framework-neutral tests use.

A test outside drf/ that needs a throttle rate or a trusted proxy count asks for
these fixtures and never patches DRF itself. Moving the API to another framework
rewrites this module, not the tests that use it. The root conftest.py loads it
with pytest_plugins.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.conf import settings
from django.test import override_settings
from rest_framework.throttling import SimpleRateThrottle

if TYPE_CHECKING:
    from conftest import ThrottleRates
    from conftest import TrustedProxies


@pytest.fixture
def throttle_rates(monkeypatch: pytest.MonkeyPatch) -> ThrottleRates:
    """
    Return a function that sets the API throttle rates for the current test.

    DRF binds the rates when its throttle classes are imported, so a settings
    override never reaches them: this patches SimpleRateThrottle.THROTTLE_RATES.
    """

    def set_rates(rates: dict[str, str]) -> None:
        monkeypatch.setattr(SimpleRateThrottle, "THROTTLE_RATES", rates)

    return set_rates


@pytest.fixture
def trusted_proxies() -> TrustedProxies:
    """Return a function that overrides how many proxies the API trusts."""

    def count(value: int) -> override_settings:
        return override_settings(
            TRUSTED_PROXY_COUNT=value,
            REST_FRAMEWORK={**settings.REST_FRAMEWORK, "NUM_PROXIES": value},
        )

    return count
