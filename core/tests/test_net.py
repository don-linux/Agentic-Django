from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

import pytest
from axes.helpers import get_client_ip_address
from django.test import RequestFactory
from django.test import override_settings

from core.net import get_client_ip

if TYPE_CHECKING:
    from django.http import HttpRequest

    from conftest import TrustedProxies


def _request(xff: str | None = None) -> HttpRequest:
    extra: dict[str, Any] = {"REMOTE_ADDR": "172.18.0.1"}
    if xff is not None:
        extra["HTTP_X_FORWARDED_FOR"] = xff
    return RequestFactory().get("/", **extra)


@pytest.mark.parametrize(
    ("num_proxies", "xff", "expected"),
    [
        (0, "203.0.113.7", "172.18.0.1"),
        (1, None, "172.18.0.1"),
        (1, "203.0.113.7", "203.0.113.7"),
        (1, "6.6.6.6, 203.0.113.7", "203.0.113.7"),
        (2, "6.6.6.6, 203.0.113.7, 10.0.0.2", "203.0.113.7"),
        (3, "203.0.113.7, 10.0.0.2", "203.0.113.7"),
    ],
)
def test_client_ip_trusts_only_the_configured_proxies(
    num_proxies: int,
    xff: str | None,
    expected: str,
    trusted_proxies: TrustedProxies,
) -> None:
    with trusted_proxies(num_proxies):
        assert get_client_ip(_request(xff)) == expected


@pytest.mark.parametrize("xff", ["   ", ",", "  ,  "])
def test_a_header_without_an_address_falls_back_to_remote_addr(
    xff: str, trusted_proxies: TrustedProxies
) -> None:
    with trusted_proxies(1):
        assert get_client_ip(_request(xff)) == "172.18.0.1"


@pytest.mark.parametrize(
    ("num_proxies", "xff", "expected"),
    [
        (1, "6.6.6.6, ", "6.6.6.6"),
        (1, "6.6.6.6,,", "6.6.6.6"),
        (2, "203.0.113.7, , 10.0.0.2", "203.0.113.7"),
    ],
)
def test_blank_hops_are_skipped(
    num_proxies: int, xff: str, expected: str, trusted_proxies: TrustedProxies
) -> None:
    with trusted_proxies(num_proxies):
        assert get_client_ip(_request(xff)) == expected


def test_axes_uses_the_shared_client_ip_rule(trusted_proxies: TrustedProxies) -> None:
    with trusted_proxies(1):
        request = _request("6.6.6.6, 203.0.113.7")

        assert get_client_ip_address(request) == "203.0.113.7"


def test_the_proxy_count_is_the_django_setting() -> None:
    # The client IP must not depend on the API framework (docs/api.md#throttling).
    with override_settings(TRUSTED_PROXY_COUNT=1):
        assert get_client_ip(_request("6.6.6.6, 203.0.113.7")) == "203.0.113.7"
