from __future__ import annotations

from http import HTTPStatus

from django.http import Http404
from ninja import Router
from ninja.testing import TestClient

from core.tests.ninja.support import make_api


def test_json_error_handlers_lock_the_detail_text() -> None:
    api = make_api(throttle=[], auth=None)
    router = Router()

    @router.get("/missing/", auth=None)
    def missing(request):
        msg = "Not found."
        raise Http404(msg)

    api.add_router("/", router)
    response = TestClient(api).get("/missing/")
    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()["detail"] == "Not found."
