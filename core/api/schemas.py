"""Shared Pydantic schemas for the Ninja API."""

from __future__ import annotations

import datetime as dt
from typing import Annotated
from typing import Any

from ninja import Schema
from pydantic import AfterValidator
from pydantic import ConfigDict
from pydantic import Field
from pydantic import PlainSerializer
from pydantic import WithJsonSchema


def _as_utc(value: dt.datetime) -> dt.datetime:
    # A naive datetime in a request is UTC, whatever TIME_ZONE is (DRF
    # DateTimeField(default_timezone=UTC) in the previous UTCDateTimeField).
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def _iso_z(value: dt.datetime) -> str:
    # DRF ISO_8601 output: isoformat() with "Z"; microseconds kept, which
    # DjangoJSONEncoder (Ninja's renderer) would cut to milliseconds.
    return _as_utc(value).isoformat().replace("+00:00", "Z")


# Use for every datetime field of /api/v1/ (docs/api.md#representation).
UTCDateTime = Annotated[
    dt.datetime,
    AfterValidator(_as_utc),
    PlainSerializer(_iso_z, return_type=str),
    WithJsonSchema({"type": "string", "format": "date-time"}),
]


class BaseSchema(Schema):
    """Output/shared schema. Do not set extra='forbid' here.

    Ninja 1.8.0 Schema._run_root_validator calls handler(values) on the raw
    ORM object when extra='forbid', which skips resolve_* and breaks response
    serialization (ninja/schema.py). Keep forbid on input schemas, and on a
    schema Ninja never builds from a model instance, such as ErrorSchema.
    """

    model_config = ConfigDict(from_attributes=True)


class InputSchema(Schema):
    # A body key that is not a field is a 400 unknown_field, not a typo ignored.
    # Strings are kept as sent, so maxLength applies to the value the client
    # sent. See docs/api.md#representation and ADR 0018.
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ErrorSchema(Schema):
    # An error body carries only these keys (core.errors.ErrorBody). A closed
    # model publishes additionalProperties: false, which keeps the 400 oneOf
    # unambiguous: a validation body has "fields", which this schema refuses.
    # extra="forbid" is safe here, unlike on BaseSchema: Ninja never builds an
    # error body from a model instance, so no resolve_* is skipped.
    model_config = ConfigDict(extra="forbid")

    code: str
    detail: str
    extra: dict[str, object] = Field(
        default_factory=dict,
        description="Structured context for some domain errors.",
    )


class ValidationErrorSchema(BaseSchema):
    code: str
    detail: str
    fields: dict[str, Any]
