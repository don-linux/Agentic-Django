"""Input schemas refuse unknown fields; output schemas do not use extra=forbid."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.users.api.views import UserOut
from apps.users.api.views import UserUpdateIn
from apps.users.tests.factories import UserFactory
from core.api.schemas import ErrorSchema
from core.api.schemas import InputSchema


def test_input_schema_forbids_unknown_fields() -> None:
    class Payload(InputSchema):
        name: str

    with pytest.raises(ValidationError):
        Payload.model_validate({"name": "Ada", "extra": 1})


@pytest.mark.django_db
def test_an_output_schema_reads_a_model_that_has_more_attributes() -> None:
    # extra='forbid' on output breaks Ninja resolve_* (ninja/schema.py): the
    # model carries every column, the schema publishes three fields.
    user = UserFactory()
    assert UserOut.from_orm(user).model_dump() == {
        "id": user.public_id,
        "name": user.name,
        "url": f"/api/v1/users/{user.public_id}/",
    }


def test_user_update_in_forbids_the_read_only_id_and_url() -> None:
    with pytest.raises(ValidationError) as excinfo:
        UserUpdateIn.model_validate({"name": "Ada", "id": "x", "url": "/"})
    errors = excinfo.value.errors()
    assert {error["loc"] for error in errors} == {("id",), ("url",)}
    assert {error["type"] for error in errors} == {"extra_forbidden"}


def test_user_out_requires_id_name_url() -> None:
    fields = UserOut.model_fields
    required = {name for name, field in fields.items() if field.is_required()}
    assert set(fields) == required == {"id", "name", "url"}


def test_the_error_schema_is_closed_like_an_error_body() -> None:
    # Pydantic's own extra="forbid": the published schema has
    # additionalProperties: false, and a key an error body never has is refused.
    with pytest.raises(ValidationError) as excinfo:
        ErrorSchema.model_validate({"code": "x", "detail": "y", "fields": {}})
    assert [error["type"] for error in excinfo.value.errors()] == ["extra_forbidden"]
    assert ErrorSchema.model_validate({"code": "x", "detail": "y"}).extra == {}
    assert ErrorSchema.model_json_schema()["additionalProperties"] is False


@pytest.mark.django_db
def test_user_url_without_a_request_is_relative() -> None:
    user = UserFactory()
    assert UserOut.resolve_url(user) == f"/api/v1/users/{user.public_id}/"
