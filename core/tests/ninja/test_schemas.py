"""Input schemas refuse unknown fields; output schemas do not use extra=forbid."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.users.api.views import UserOut
from apps.users.api.views import UserUpdateIn
from apps.users.tests.factories import UserFactory
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


def test_user_update_in_ignores_read_only_id_and_url() -> None:
    payload = UserUpdateIn.model_validate({"name": "Ada", "id": "x", "url": "/"})
    assert payload.name == "Ada"
    assert "id" not in payload.model_dump(exclude_unset=True)
    assert "url" not in payload.model_dump(exclude_unset=True)


def test_user_out_requires_id_name_url() -> None:
    fields = UserOut.model_fields
    required = {name for name, field in fields.items() if field.is_required()}
    assert set(fields) == required == {"id", "name", "url"}
