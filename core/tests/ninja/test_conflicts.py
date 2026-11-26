"""A duplicate is a 409 the operation declares (docs/api.md#duplicates, ADR 0023).

The schema cannot say a value must be unused, so the 400 of full_clean() would
reject a request the schema allows; schemathesis accepts a 409 for it. The
service checks the value and raises ConflictError with already_exists.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

from django.http import HttpRequest  # noqa: TC002  # runtime: Ninja signature
from ninja import Router
from ninja import Status
from ninja.testing import TestClient

from core.api.responses import CONFLICT_ERRORS
from core.api.responses import WRITE_ERRORS
from core.api.responses import response_docs
from core.api.schemas import BaseSchema
from core.api.schemas import InputSchema
from core.exceptions import ConflictError
from core.tests.ninja.support import make_api

if TYPE_CHECKING:
    from ninja import NinjaAPI


class _CodeIn(InputSchema):
    code: str


class _CodeOut(BaseSchema):
    code: str


ERRORS = {**WRITE_ERRORS, **CONFLICT_ERRORS}


def _api_and_client() -> tuple[NinjaAPI, TestClient]:
    api = make_api(throttle=[])
    router = Router()
    taken: set[str] = set()

    @router.post(
        "/things/",
        auth=None,
        response={201: _CodeOut, **ERRORS},
        openapi_extra=response_docs(ERRORS),
    )
    def create(request: HttpRequest, body: _CodeIn) -> Status[_CodeOut]:
        # What the service does inside its transaction, before full_clean().
        if body.code in taken:
            msg = "A thing with this code already exists."
            raise ConflictError(msg, code="already_exists", extra={"field": "code"})
        taken.add(body.code)
        return Status(HTTPStatus.CREATED, _CodeOut(code=body.code))

    api.add_router("/", router)
    return api, TestClient(api)


def _post(client: TestClient, code: str):
    # ninja.testing builds a Mock request: content_type must be set explicitly.
    return client.post("/things/", json={"code": code}, content_type="application/json")


def test_a_duplicate_is_409_already_exists() -> None:
    _, client = _api_and_client()
    assert _post(client, "A-1").status_code == HTTPStatus.CREATED
    response = _post(client, "A-1")
    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json() == {
        "code": "already_exists",
        "detail": "A thing with this code already exists.",
        "extra": {"field": "code"},
    }


def test_the_operation_declares_the_409_with_its_description() -> None:
    api, _ = _api_and_client()
    # path_prefix: the test API is not mounted, so Ninja cannot reverse its root.
    schema = api.get_openapi_schema(path_prefix="")
    responses = schema["paths"]["/things/"]["post"]["responses"]
    assert responses[409]["description"].startswith("Conflicts with the stored data")
