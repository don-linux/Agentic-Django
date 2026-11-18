"""Client IP resolution for django-axes and anything else that needs the client IP."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings

if TYPE_CHECKING:
    from django.http import HttpRequest


def get_client_ip(request: HttpRequest) -> str:
    """
    Return the client IP: the TRUSTED_PROXY_COUNT-th X-Forwarded-For entry from the end.

    DRF's NUM_PROXIES rule, but blank hops are skipped, so a trailing comma never
    gives every client the same empty IP. Reads the Django setting, not DRF's, so
    the rule outlives the API framework. See docs/api.md#throttling.
    """
    remote_addr: str = request.META.get("REMOTE_ADDR", "")
    num_proxies: int = settings.TRUSTED_PROXY_COUNT
    forwarded_for = request.headers.get("X-Forwarded-For")
    if not num_proxies or not forwarded_for:
        return remote_addr
    addresses = [hop.strip() for hop in forwarded_for.split(",") if hop.strip()]
    if not addresses:
        return remote_addr
    return addresses[-min(num_proxies, len(addresses))]
