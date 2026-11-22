"""
The only framework-aware fixtures that framework-neutral tests use.

A test outside tests/drf/ that needs a throttle rate or a trusted proxy count
asks for these fixtures and never patches Ninja itself. The root conftest.py
loads it with pytest_plugins.

Re-pointed from DRF's SimpleRateThrottle to Ninja 1.8.0's
ninja.throttling.SimpleRateThrottle and NINJA_NUM_PROXIES.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.test import override_settings
from ninja.throttling import SimpleRateThrottle

if TYPE_CHECKING:
    from conftest import ThrottleRates
    from conftest import TrustedProxies


@pytest.fixture
def throttle_rates(monkeypatch: pytest.MonkeyPatch) -> ThrottleRates:
    """
    Return a function that sets the API throttle rates for the current test.

    Ninja parses the rate when a throttle instance is created, but our
    AnonThrottle/UserThrottle re-read THROTTLE_RATES on each request.
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
            NINJA_NUM_PROXIES=value,
        )

    return count
