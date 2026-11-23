"""A download operation publishes its file type and the S3 redirect.

core.api.responses.download_docs gives the 200 the file's media type, which
ApiContractMiddleware negotiates, and declares the 302 to a signed URL that
core.files.protected_file_response answers on object storage.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest  # runtime: Ninja signature
from django.http import HttpResponse
from ninja import Router

from core.api.negotiation import _produces
from core.api.responses import LOOKUP_ERRORS
from core.api.responses import download_docs
from core.tests.ninja.support import make_api


def _operation() -> dict[str, Any]:
    api = make_api(throttle=[])
    router = Router()

    @router.get(
        "/files/",
        auth=None,
        response={200: None, 302: None, **LOOKUP_ERRORS},
        openapi_extra=download_docs("application/pdf", LOOKUP_ERRORS),
    )
    def download(request: HttpRequest) -> HttpResponse:
        return HttpResponse(b"%PDF", content_type="application/pdf")

    api.add_router("/", router)
    # path_prefix: the test API is not mounted, so Ninja cannot reverse its root.
    return api.get_openapi_schema(path_prefix="")["paths"]["/files/"]["get"]


def test_the_200_is_the_file_and_the_302_a_signed_url() -> None:
    responses = _operation()["responses"]
    assert responses[200]["content"] == {
        "application/pdf": {"schema": {"type": "string", "format": "binary"}}
    }
    assert "Location" in responses[302]["headers"]
    # The shared error docs are still there.
    assert responses[404]["description"]


def test_the_download_negotiates_its_file_type() -> None:
    assert _produces({"GET": _operation()}, "GET") == ["application/pdf"]
