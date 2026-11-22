from __future__ import annotations

from http import HTTPStatus

from django.core.exceptions import PermissionDenied
from ninja import Router
from ninja.errors import Throttled
from ninja.testing import TestClient

from core.exceptions import ApplicationError
from core.exceptions import ConflictError
from core.tests.ninja.support import make_api


def _client(raises, method="get"):
    api = make_api(throttle=[], auth=None)
    router = Router()

    @router.api_operation(["GET", "POST"], "/err/", auth=None)
    def err(request):
        raises()
        return {"ok": True}

    api.add_router("/", router)
    return TestClient(api)


def test_application_error_maps_to_its_status_and_code() -> None:
    def raises() -> None:
        msg = "Gone."
        raise ApplicationError(msg, code="gone", status_code=HTTPStatus.GONE)

    response = _client(raises).get("/err/")
    assert response.status_code == HTTPStatus.GONE
    assert response.json() == {"code": "gone", "detail": "Gone."}


def test_conflict_error_defaults_to_409_and_includes_extra() -> None:
    def raises() -> None:
        msg = "Taken."
        raise ConflictError(msg, code="taken", extra={"field": "email"})

    response = _client(raises).get("/err/")
    assert response.status_code == HTTPStatus.CONFLICT
    body = response.json()
    assert body["code"] == "taken"
    assert body["detail"] == "Taken."
    assert body.get("extra") == {"field": "email"}


def test_django_permission_denied_maps_to_permission_denied() -> None:
    def raises() -> None:
        raise PermissionDenied

    response = _client(raises).get("/err/")
    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.json()["code"] == "permission_denied"


def test_throttled_keeps_retry_after_header() -> None:
    wait = 7

    def raises() -> None:
        raise Throttled(wait=wait)

    response = _client(raises).get("/err/")
    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert response.json()["code"] == "throttled"
    assert int(response.headers["Retry-After"]) == wait
