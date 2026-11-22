"""
Native Ninja throttles with get_ident from core.net.

AnonThrottle, UserThrottle and ScopedThrottle extend Ninja's AnonRateThrottle,
UserRateThrottle and SimpleRateThrottle. get_ident uses TRUSTED_PROXY_COUNT via
core.net, skipping blank hops. A rate passed to the constructor ("10/min", as in
Ninja's docs) is fixed. A rate from NINJA_DEFAULT_THROTTLE_RATES is re-read on
each request, so tests can patch SimpleRateThrottle.THROTTLE_RATES
(core/tests/ninja/adapters.py). Ninja 1.8.0 ninja/throttling.py does not catch
cache errors.

Sources: django-ninja 1.8.0 ninja/throttling.py and ninja/conf.py
(NINJA_NUM_PROXIES, NINJA_DEFAULT_THROTTLE_RATES); Context7
/websites/django-ninja_dev "Configure Global Throttling".
"""

from __future__ import annotations

import copy
import threading
from typing import TYPE_CHECKING

from ninja.throttling import AnonRateThrottle
from ninja.throttling import SimpleRateThrottle
from ninja.throttling import UserRateThrottle

from core.net import get_client_ip

if TYPE_CHECKING:
    from django.http import HttpRequest


# One throttle instance serves every request of a process (core/api/api.py), and
# Ninja's SimpleRateThrottle keeps key/history/now on it. DRF built one per request
# (APIView.get_throttles). Each check runs on a per-thread copy instead; Ninja calls
# wait() right after allow_request() in the same thread (ninja/operation.py).
_STATE = threading.local()


class IdentMixin(SimpleRateThrottle):
    def get_ident(self, request: HttpRequest) -> str:
        return get_client_ip(request)


class RefreshingRateMixin(SimpleRateThrottle):
    """Per-request copy, with the scope rate re-read so settings patches apply."""

    def __init__(self, rate: str | None = None) -> None:
        # An explicit rate ("10/min" as in Ninja's docs) is fixed; only a rate
        # read from NINJA_DEFAULT_THROTTLE_RATES is refreshed per request.
        self._fixed_rate = rate
        super().__init__(rate)

    def allow_request(self, request: HttpRequest) -> bool:
        worker: RefreshingRateMixin = copy.copy(self)
        worker.rate = self._fixed_rate or worker.get_rate()
        worker.num_requests, worker.duration = worker.parse_rate(worker.rate)
        setattr(_STATE, f"throttle_{id(self)}", worker)
        return super(RefreshingRateMixin, worker).allow_request(request)

    def wait(self) -> float | None:
        worker: RefreshingRateMixin = getattr(_STATE, f"throttle_{id(self)}", self)
        return super(RefreshingRateMixin, worker).wait()


class AnonThrottle(IdentMixin, RefreshingRateMixin, AnonRateThrottle):
    pass


class UserThrottle(IdentMixin, RefreshingRateMixin, UserRateThrottle):
    pass


class ScopedThrottle(IdentMixin, RefreshingRateMixin, SimpleRateThrottle):
    """SimpleRateThrottle for a named per-endpoint scope (DRF's ScopedRateThrottle)."""

    def __init__(self, rate: str | None = None, scope: str | None = None) -> None:
        if scope is not None:
            self.scope = scope
        super().__init__(rate)

    def get_cache_key(self, request: HttpRequest) -> str:
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            ident = str(user.pk)
        else:
            ident = self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}
