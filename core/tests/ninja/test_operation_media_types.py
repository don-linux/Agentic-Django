"""ApiContractMiddleware negotiates the media types each operation declares.

An operation produces the content of its 2xx responses in the OpenAPI
document: JSON for every operation the template ships, a file type for a
download that declares one (docs/files.md). One that declares no content on
success, such as a 204, has nothing to negotiate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

import pytest

from core.api.negotiation import _acceptable
from core.api.negotiation import _produces

if TYPE_CHECKING:
    from django.test import RequestFactory

JSON = "application/json"
DOWNLOAD: dict[str, Any] = {
    "GET": {
        "responses": {
            "200": {"content": {"application/pdf": {}}},
            "404": {"content": {JSON: {}}},
        }
    }
}
DELETE: dict[str, Any] = {
    "DELETE": {
        "responses": {
            "204": {"description": "No Content"},
            "404": {"content": {JSON: {}}},
        }
    }
}


def test_an_operation_produces_what_its_success_declares() -> None:
    assert _produces(DOWNLOAD, "GET") == ["application/pdf"]
    # HEAD runs the GET operation.
    assert _produces(DOWNLOAD, "HEAD") == ["application/pdf"]
    assert _produces(DELETE, "DELETE") == []


def test_a_media_type_is_compared_without_its_parameters() -> None:
    operations: dict[str, Any] = {
        "GET": {"responses": {"200": {"content": {f"{JSON}; v=1": {}}}}}
    }
    assert _produces(operations, "GET") == [JSON]


@pytest.mark.parametrize(
    ("accept", "acceptable"),
    [
        ("application/pdf", True),
        ("application/*", True),
        ("*/*", True),
        # The negative case: the download does not produce JSON.
        ("application/json", False),
    ],
)
def test_a_download_negotiates_its_own_media_type(
    rf: RequestFactory, accept: str, *, acceptable: bool
) -> None:
    request = rf.get("/", headers={"Accept": accept})
    assert _acceptable(request, _produces(DOWNLOAD, "GET")) is acceptable


def test_an_operation_without_content_has_nothing_to_negotiate(
    rf: RequestFactory,
) -> None:
    request = rf.delete("/", headers={"Accept": "text/html"})
    assert _acceptable(request, _produces(DELETE, "DELETE")) is True
