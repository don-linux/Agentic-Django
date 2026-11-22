"""
Native Ninja throttles with get_ident from core.net.

AnonThrottle, UserThrottle and ScopedThrottle extend Ninja's AnonRateThrottle,
UserRateThrottle and SimpleRateThrottle. get_ident uses TRUSTED_PROXY_COUNT via
core.net, skipping blank hops. The rate is re-read from
NINJA_DEFAULT_THROTTLE_RATES on each request, so tests can patch
SimpleRateThrottle.THROTTLE_RATES (core/tests/ninja/adapters.py). Ninja 1.8.0
ninja/throttling.py does not catch cache errors.

Sources: django-ninja 1.8.0 ninja/throttling.py and ninja/conf.py
(NINJA_NUM_PROXIES, NINJA_DEFAULT_THROTTLE_RATES); Context7
/websites/django-ninja_dev "Configure Global Throttling".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ninja.throttling import AnonRateThrottle
from ninja.throttling import SimpleRateThrottle
from ninja.throttling import UserRateThrottle

from core.net import get_client_ip

if TYPE_CHECKING:
    from django.http import HttpRequest


class IdentMixin(SimpleRateThrottle):
    def get_ident(self, request: HttpRequest) -> str:
        return get_client_ip(request)


class RefreshingRateMixin(SimpleRateThrottle):
    """Re-parse the scope rate on each request so settings patches take effect."""

    def allow_request(self, request: HttpRequest) -> bool:
        self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)
        return super().allow_request(request)


class AnonThrottle(IdentMixin, RefreshingRateMixin, AnonRateThrottle):
    pass


class UserThrottle(IdentMixin, RefreshingRateMixin, UserRateThrottle):
    pass


class ScopedThrottle(IdentMixin, RefreshingRateMixin, SimpleRateThrottle):
    """SimpleRateThrottle for a named per-endpoint scope."""

    def __init__(self, rate: str | None = None, scope: str | None = None) -> None:
        if scope is not None:
            self.scope = scope
        super().__init__(rate)
