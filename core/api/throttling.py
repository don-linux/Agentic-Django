"""
Ninja's throttles, with the client IP from core.net.

AnonThrottle, UserThrottle and ScopedThrottle extend Ninja's AnonRateThrottle,
UserRateThrottle and SimpleRateThrottle. get_ident is core.net.get_client_ip
(TRUSTED_PROXY_COUNT, blank hops skipped). A rate passed to the constructor
("10/min", as in Ninja's docs) is fixed. A rate from
NINJA_DEFAULT_THROTTLE_RATES is read again from SimpleRateThrottle.THROTTLE_RATES
on each request, which is what the throttle_rates test fixture patches
(core/tests/ninja/adapters.py). Ninja does not catch cache errors: ValkeyCache
fails open, so while Valkey is down the throttles stop counting instead of
failing requests (docs/security.md#valkey-outages). See
https://django-ninja.dev/guide/throttling/ and django-ninja 1.8.0
ninja/throttling.py.
"""

from __future__ import annotations

import copy
import threading
from typing import TYPE_CHECKING
from typing import override

from ninja.throttling import AnonRateThrottle
from ninja.throttling import SimpleRateThrottle
from ninja.throttling import UserRateThrottle

from core.net import get_client_ip

if TYPE_CHECKING:
    from django.http import HttpRequest


# One throttle instance serves every request of a process (core/api/api.py), and
# Ninja's SimpleRateThrottle keeps key/history/now on it, which threads would
# share. Each check runs on a per-thread copy instead; Ninja calls
# wait() right after allow_request() in the same thread (ninja/operation.py);
# core/tests/ninja/test_throttle_call_order.py pins that order.
_STATE = threading.local()


class IdentMixin(SimpleRateThrottle):
    @override
    def get_ident(self, request: HttpRequest) -> str:
        return get_client_ip(request)


class RefreshingRateMixin(SimpleRateThrottle):
    """Checks each request on a per-thread copy, with the scope's rate read again."""

    def __init__(self, rate: str | None = None) -> None:
        # An explicit rate ("10/min" as in Ninja's docs) is fixed; only a rate
        # read from NINJA_DEFAULT_THROTTLE_RATES is refreshed per request.
        self._fixed_rate = rate
        super().__init__(rate)

    @override
    def allow_request(self, request: HttpRequest) -> bool:
        worker: RefreshingRateMixin = copy.copy(self)
        worker.rate = self._fixed_rate or worker.get_rate()
        worker.num_requests, worker.duration = worker.parse_rate(worker.rate)
        setattr(_STATE, f"throttle_{id(self)}", worker)
        return super(RefreshingRateMixin, worker).allow_request(request)

    @override
    def wait(self) -> float | None:
        worker: RefreshingRateMixin = getattr(_STATE, f"throttle_{id(self)}", self)
        return super(RefreshingRateMixin, worker).wait()


class AnonThrottle(IdentMixin, RefreshingRateMixin, AnonRateThrottle):
    pass


class UserThrottle(IdentMixin, RefreshingRateMixin, UserRateThrottle):
    pass


class ScopedThrottle(IdentMixin, RefreshingRateMixin, SimpleRateThrottle):
    """SimpleRateThrottle for a named per-endpoint scope, per user or client IP."""

    def __init__(self, rate: str | None = None, scope: str | None = None) -> None:
        if scope is not None:
            self.scope = scope
        super().__init__(rate)

    @override
    def get_cache_key(self, request: HttpRequest) -> str:
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            ident = str(user.pk)
        else:
            ident = self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}
