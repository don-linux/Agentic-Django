"""URL route helpers shared by the convention tests."""

from __future__ import annotations

import re

from django.urls import URLPattern
from django.urls import URLResolver


def _routes(patterns: list[URLPattern | URLResolver], prefix: str = "") -> list[str]:
    routes = []
    for pattern in patterns:
        route = prefix + str(pattern.pattern).removeprefix("^").removesuffix("$")
        if isinstance(pattern, URLResolver):
            routes += _routes(pattern.url_patterns, route)
        else:
            routes.append(route)
    return routes


def api_integer_pk_route_problems(routes: list[str]) -> list[str]:
    """Return /api/ routes whose path captures pk or a bare integer id.

    `(?P<id>` plus a UUID regex is the public identifier (`public_id`), not the
    integer pk.
    """
    uuid_id = re.compile(r"\(\?P<id>\[[0-9a-fA-F-]+\]\{8\}-\[[0-9a-fA-F-]+\]\{4\}-")
    capture = re.compile(r"\(\?P<(pk|id)>|<int:(pk|id)>|<(pk|id)>")
    problems = []
    for route in routes:
        if not route.startswith("api/"):
            continue
        if uuid_id.search(route):
            continue
        if capture.search(route):
            problems.append(route)
    return problems
