from __future__ import annotations

from ninja import NinjaAPI

from core.api.api import api


def test_contract_api_keeps_docs_and_schema_urls_custom() -> None:
    # Built-in Ninja docs/schema are off; negotiation.py serves the existing names.
    assert api.openapi_url is None
    assert api.docs_url is None
    assert isinstance(api, NinjaAPI)
