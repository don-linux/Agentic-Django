"""Shared Pydantic schemas for the Ninja API."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING
from typing import Annotated
from typing import Any
from typing import Final
from typing import TypeIs
from uuid import UUID

from ninja import FilterSchema
from ninja import HeaderEx
from ninja import P
from ninja import PathEx
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
from pydantic_core import core_schema

from core.api.converters import PublicIdConverter

if TYPE_CHECKING:
    from pydantic import GetCoreSchemaHandler
    from pydantic import GetJsonSchemaHandler
    from pydantic.json_schema import JsonSchemaValue

# RFC 3339, section 5.6: full-date "T" full-time, where the time offset ("Z" or
# +hh:mm) is required; "T" and "Z" may be lowercase. ASCII digits only. A
# request's year is 1000 to 9998: Python's datetime ends at years 1 and 9999,
# and an offset would push 0001-01-01T00:00:00+00:01 past it in UTC.
_FULL_DATE = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
_YEAR_1000_TO_9998 = r"(?:[1-8][0-9]{3}|9[0-8][0-9]{2}|99[0-8][0-9]|999[0-8])"
DATE_TIME_PATTERN = (
    _YEAR_1000_TO_9998
    + r"-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?"
    + r"(?:[Zz]|[+-][0-9]{2}:[0-9]{2})"
)
_DATE_TIME = re.compile(DATE_TIME_PATTERN)
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
_INT_TYPE: Final = "int_type"
_STRING_TYPE: Final = "string_type"
_STRING_PATTERN_MISMATCH: Final = "string_pattern_mismatch"


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
            _DATETIME_PARSING,
            {"error": "expected RFC 3339 with a time zone, in years 1000 to 9998"},
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
    # Ninja publishes a request in "validation" mode and a response in
    # "serialization" mode: a request declares the pattern it must match, and a
    # response is any UTC date-time, which can fall in year 999 or 9999.
    WithJsonSchema(
        {"type": "string", "format": "date-time", "pattern": f"^{DATE_TIME_PATTERN}$"},
        mode="validation",
    ),
    WithJsonSchema({"type": "string", "format": "date-time"}, mode="serialization"),
]

# Use for every date field of /api/v1/ (docs/api.md#representation). Ninja's
# encoder writes a date as YYYY-MM-DD.
ISODate = Annotated[dt.date, BeforeValidator(_rfc3339_date)]

# Use for every id a request sends, such as a related object's public_id
# (docs/api.md#representation). A path id already goes through the converter.
PublicId = Annotated[UUID, BeforeValidator(_hyphenated_uuid)]

# The id of a detail path, "/{public_id:id}/": the view takes it as
# `<model>_id: PublicIdPath`. The alias keeps the contract's name, id, without
# an argument that shadows the builtin (guide/path-params/), and the schema
# publishes the pattern the converter matches. PathEx is the type-checker
# spelling of Path[T, P(...)].
PublicIdPath = PathEx[
    Annotated[
        UUID,
        WithJsonSchema({"type": "string", "pattern": f"^{PublicIdConverter.regex}$"}),
    ],
    P(alias="id"),
]


# The If-Match header of a write that checks a precondition (core.concurrency).
# Required in the contract, so a request without it is a 400 like any missing
# parameter, and a stale one is a 412 (docs/api.md#optimistic-concurrency).
IfMatch = HeaderEx[
    str,
    P(alias="If-Match", description="The ETag of the version the client read."),
]


def _json_integer(value: object) -> object:
    # JSON Schema's integer is any number with a zero fractional part, so 2.0
    # is one (JSON Schema 2020-12, section 4.2.1), and Pydantic turns it into 2
    # and refuses 2.5 (int_from_float). Pydantic would also take true (a bool is
    # an int in Python) and "2", which the schema does not allow.
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise PydanticKnownError(_INT_TYPE)
    return value


# Annotated metadata for an IntegerChoices or an integer Literal in a request
# body, `Annotated[Priority, JsonInteger]`: only a JSON number with no
# fraction passes (docs/api.md#field-types). Text choices need nothing.
JsonInteger = BeforeValidator(_json_integer)

# Use for every integer field of a request body, with the model field's range
# as ge and le (docs/api.md#field-types). Pydantic's StrictInt refuses 2.0,
# which the schema allows.
JsonInt = Annotated[int, JsonInteger]


def _plain_decimal(value: Decimal) -> str:
    # Positional notation: str() would write 1E+2 for a computed Decimal.
    return format(value, "f")


# Any decimal a response can carry: an optional sign, digits, and an optional
# fraction, never an exponent.
_ANY_DECIMAL = r"-?[0-9]+(?:\.[0-9]+)?"


@dataclass(frozen=True)
class DecimalString:
    """A decimal that travels as a JSON string, at the model field's precision.

    `Annotated[Decimal, DecimalString(max_digits=10, decimal_places=2)]`, with
    the model's max_digits and decimal_places: a request sends "12.50", never a
    JSON number, which the parser would read through a float and round. The
    schema publishes the exact pattern, so full_clean() never refuses a value
    the schema allows. `negative=True` lets a minus sign through.
    """

    max_digits: int
    decimal_places: int
    negative: bool = False

    @property
    def pattern(self) -> str:
        whole_digits = self.max_digits - self.decimal_places
        whole = f"[0-9]{{1,{whole_digits}}}" if whole_digits else "0"
        places = self.decimal_places
        fraction = f"(?:\\.[0-9]{{1,{places}}})?" if places else ""
        sign = "-?" if self.negative else ""
        return f"{sign}{whole}{fraction}"

    def _validate(self, value: object) -> object:
        # A Decimal comes from server code (a model, a response), never from JSON.
        if isinstance(value, Decimal):
            return value
        if not isinstance(value, str):
            raise PydanticKnownError(_STRING_TYPE)
        if not re.fullmatch(self.pattern, value):
            raise PydanticKnownError(
                _STRING_PATTERN_MISMATCH, {"pattern": self.pattern}
            )
        return value

    def __get_pydantic_core_schema__(
        self, source: type[Any], handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_before_validator_function(
            self._validate,
            handler(source),
            serialization=core_schema.plain_serializer_function_ser_schema(
                _plain_decimal, return_schema=core_schema.str_schema()
            ),
        )

    def __get_pydantic_json_schema__(
        self, schema: core_schema.CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        # Ninja publishes a request in "validation" mode and a response in
        # "serialization" mode, as for UTCDateTime.
        pattern = self.pattern if handler.mode == "validation" else _ANY_DECIMAL
        return {"type": "string", "pattern": f"^{pattern}$"}


class BaseSchema(Schema):
    """Output/shared schema. Do not set extra='forbid' here.

    Ninja 1.8.0 Schema._run_root_validator calls handler(values) on the raw
    ORM object when extra='forbid', which skips resolve_* and breaks response
    serialization (ninja/schema.py). Keep forbid on input schemas, and on a
    schema Ninja never builds from a model instance, such as ErrorSchema.
    """


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
    row is saved. The walk keeps its own stack, so only the JSON decoder
    limits how deep a value may be.
    """
    pending: list[object] = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if "\x00" in item:
                raise PydanticCustomError(NUL_CODE, NUL_DETAIL)
            try:
                item.encode()
            except UnicodeEncodeError:
                raise PydanticKnownError(_STRING_UNICODE) from None
        elif _is_mapping(item):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif _is_collection(item):
            pending.extend(item)
    return value


# Use for a free-text query parameter, such as a search box: PostgreSQL
# cannot take a NUL in the SQL either (docs/api.md#filtering-search-and-ordering).
QueryText = Annotated[str, AfterValidator(storable_text)]


class BaseFilterSchema(FilterSchema):
    """A list's filters, read from the query string with Query[...].

    Every field goes through storable_text, as in InputSchema. Unknown query
    parameters stay ignored (ADR 0021). See
    docs/api.md#filtering-search-and-ordering.
    """

    @field_validator("*")
    @classmethod
    def reject_unstorable_text(cls, value: object) -> object:
        return storable_text(value)


class InputSchema(Schema):
    # A body key that is not a field is a 400 unknown_field, not a typo ignored.
    # Strings are kept as sent, so maxLength applies to the value the client
    # sent. See docs/api.md#representation and ADR 0018.
    model_config = ConfigDict(extra="forbid")

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
