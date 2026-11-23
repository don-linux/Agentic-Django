"""Shared Pydantic schemas for the Ninja API."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping
from typing import Annotated
from typing import Any
from typing import Final
from typing import TypeIs
from uuid import UUID

from ninja import Schema
from pydantic import AfterValidator
from pydantic import BeforeValidator
from pydantic import ConfigDict
from pydantic import Field
from pydantic import PlainSerializer
from pydantic import WithJsonSchema
from pydantic import field_validator
from pydantic_core import PydanticCustomError
from pydantic_core import PydanticKnownError

from core.api.converters import PublicIdConverter

# RFC 3339, section 5.6: full-date "T" full-time, where the time offset ("Z" or
# +hh:mm) is required; "T" and "Z" may be lowercase. ASCII digits only.
_FULL_DATE = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
_DATE_TIME = re.compile(
    _FULL_DATE
    + r"[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:[Zz]|[+-][0-9]{2}:[0-9]{2})"
)
_DATE = re.compile(_FULL_DATE)
# RFC 9562's hyphenated form, in either case, as the public_id path converter.
_UUID = re.compile(PublicIdConverter.regex)
# Pydantic's own error types, so the fields of a 400 read as for any other type.
_DATETIME_TYPE: Final = "datetime_type"
_DATETIME_PARSING: Final = "datetime_parsing"
_DATE_TYPE: Final = "date_type"
_DATE_PARSING: Final = "date_parsing"
_UUID_TYPE: Final = "uuid_type"
_UUID_PARSING: Final = "uuid_parsing"


def _rfc3339_date_time(value: object) -> object:
    # Pydantic would also take a number or a numeric string (a Unix time), a
    # date alone, or a value without a time zone: none is a date-time, which
    # the schema publishes (ADR 0022). A datetime comes from server code (a
    # model, a response), never from JSON.
    if isinstance(value, dt.datetime):
        return value
    if not isinstance(value, str):
        raise PydanticKnownError(_DATETIME_TYPE)
    if not _DATE_TIME.fullmatch(value):
        raise PydanticKnownError(
            _DATETIME_PARSING, {"error": "expected RFC 3339 with a time zone"}
        )
    return value


def _rfc3339_date(value: object) -> object:
    # The same for a full-date: Pydantic would also take a number, or a
    # datetime string at midnight.
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value
    if not isinstance(value, str):
        raise PydanticKnownError(_DATE_TYPE)
    if not _DATE.fullmatch(value):
        raise PydanticKnownError(_DATE_PARSING, {"error": "expected RFC 3339"})
    return value


def _as_utc(value: dt.datetime) -> dt.datetime:
    # A request always carries a time zone (_rfc3339_date_time). A naive
    # datetime can only come from server code, and is UTC whatever TIME_ZONE
    # is: with USE_TZ, saving a naive datetime warns and guesses a zone.
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def _iso_z(value: dt.datetime) -> str:
    # ISO 8601 in UTC with "Z", microseconds kept, so a client can send the
    # value back (a filter, a comparison) without losing precision; Ninja's
    # renderer, DjangoJSONEncoder, would cut it to milliseconds (ADR 0021).
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _hyphenated_uuid(value: object) -> object:
    # Pydantic would also take the form without hyphens, in braces, or as a URN;
    # the schema publishes format "uuid", the hyphenated form.
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        raise PydanticKnownError(_UUID_TYPE)
    if not _UUID.fullmatch(value):
        raise PydanticKnownError(
            _UUID_PARSING, {"error": "expected the hyphenated form"}
        )
    return value


# Use for every datetime field of /api/v1/ (docs/api.md#representation).
UTCDateTime = Annotated[
    dt.datetime,
    BeforeValidator(_rfc3339_date_time),
    AfterValidator(_as_utc),
    PlainSerializer(_iso_z, return_type=str),
    WithJsonSchema({"type": "string", "format": "date-time"}),
]

# Use for every date field of /api/v1/ (docs/api.md#representation). Ninja's
# encoder writes a date as YYYY-MM-DD.
ISODate = Annotated[dt.date, BeforeValidator(_rfc3339_date)]

# Use for every id a request sends, such as a related object's public_id
# (docs/api.md#representation). A path id already goes through the converter.
PublicId = Annotated[UUID, BeforeValidator(_hyphenated_uuid)]


class BaseSchema(Schema):
    """Output/shared schema. Do not set extra='forbid' here.

    Ninja 1.8.0 Schema._run_root_validator calls handler(values) on the raw
    ORM object when extra='forbid', which skips resolve_* and breaks response
    serialization (ninja/schema.py). Keep forbid on input schemas, and on a
    schema Ninja never builds from a model instance, such as ErrorSchema.
    """

    model_config = ConfigDict(from_attributes=True)


# PostgreSQL text cannot store NUL (\x00), and Pydantic has no check for it.
# Django's form fields refuse it with this code and message
# (ProhibitNullCharactersValidator); its model fields do not.
NUL_CODE = "null_characters_not_allowed"
NUL_DETAIL = "Null characters are not allowed."
_STRING_UNICODE: Final = "string_unicode"


def _is_mapping(value: object) -> TypeIs[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _is_collection(
    value: object,
) -> TypeIs[list[object] | tuple[object, ...] | set[object] | frozenset[object]]:
    return isinstance(value, list | tuple | set | frozenset)


def storable_text[T](value: T) -> T:
    """Return value, or raise for text PostgreSQL cannot store, at any depth.

    A NUL, or a lone surrogate (U+D800 to U+DFFF on its own), which a JSON
    escape can carry (RFC 8259, section 8.2): it is not Unicode text, and
    psycopg cannot encode it. Without this check either one is a 500 when the
    row is saved.
    """
    if isinstance(value, str):
        if "\x00" in value:
            raise PydanticCustomError(NUL_CODE, NUL_DETAIL)
        try:
            value.encode()
        except UnicodeEncodeError:
            raise PydanticKnownError(_STRING_UNICODE) from None
    elif _is_mapping(value):
        for key, item in value.items():
            storable_text(key)
            storable_text(item)
    elif _is_collection(value):
        for item in value:
            storable_text(item)
    return value


class InputSchema(Schema):
    # A body key that is not a field is a 400 unknown_field, not a typo ignored.
    # Strings are kept as sent, so maxLength applies to the value the client
    # sent. See docs/api.md#representation and ADR 0018.
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    @field_validator("*")
    @classmethod
    def reject_unstorable_text(cls, value: object) -> object:
        # Every field of every input schema; a nested one runs this itself.
        return storable_text(value)


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
