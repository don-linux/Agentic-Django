from __future__ import annotations

from core.api.api import api


def _schema() -> dict:
    return api.get_openapi_schema()


def test_every_user_field_is_required_in_responses() -> None:
    user = _schema()["components"]["schemas"]["UserOut"]
    assert set(user["required"]) == {"id", "name", "url"}


def test_request_components_keep_their_own_required_list() -> None:
    body = _schema()["components"]["schemas"]["UserUpdateIn"]
    assert "required" not in body or "name" not in body.get("required", [])


def test_a_page_always_has_its_links_even_when_they_are_null() -> None:
    page = _schema()["components"]["schemas"]["PagedUserOut"]
    assert set(page["required"]) == {"count", "next", "previous", "results"}
