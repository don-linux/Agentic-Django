from __future__ import annotations

from ninja import NinjaAPI

from core.api.api import api
from core.api.auth import staff_docs_required


def test_the_schema_and_docs_are_ninja_s_own_views_behind_the_staff_gate() -> None:
    # ADR 0019: Ninja serves both, under the same paths, through docs_decorator.
    assert api.openapi_url == "/schema/"
    assert api.docs_url == "/docs/"
    assert api.docs_decorator is staff_docs_required
    assert isinstance(api, NinjaAPI)
