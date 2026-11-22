"""Shared Pydantic schemas for the Ninja API."""

from __future__ import annotations

from typing import Any

from ninja import Schema
from pydantic import ConfigDict
from pydantic import Field


class BaseSchema(Schema):
    """Output/shared schema. Do not set extra='forbid' here.

    Ninja 1.8.0 Schema._run_root_validator calls handler(values) on the raw
    ORM object when extra='forbid', which skips resolve_* and breaks response
    serialization (ninja/schema.py). Keep forbid on input schemas only.
    """

    model_config = ConfigDict(from_attributes=True)


class InputSchema(Schema):
    # A body key that is not a field is a 400 unknown_field, not a typo ignored.
    # See docs/api.md#representation and ADR 0006.
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ErrorSchema(BaseSchema):
    code: str
    detail: str
    extra: dict[str, object] = Field(
        default_factory=dict,
        description="Structured context for some domain errors.",
    )


class FieldErrorSchema(BaseSchema):
    code: str
    detail: str


class ValidationErrorSchema(BaseSchema):
    code: str
    detail: str
    fields: dict[str, Any]
