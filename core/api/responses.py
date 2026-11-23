"""Error responses every operation declares, so docs/openapi.json lists them.

Spread them into `response=` (Ninja's documented way to declare several status
codes: https://django-ninja.dev/guide/responses/#multiple-response-schemas):

    @router.get("/", response={200: list[ThingOut], **LIST_ERRORS})
    @router.get("/{id}/", response={200: ThingOut, **LOOKUP_ERRORS})
    @router.patch("/{id}/", response={200: ThingOut, **LOOKUP_ERRORS, **WRITE_ERRORS})

The descriptions and the WWW-Authenticate / Retry-After headers are added by
the documented operation openapi_extra option, from RESPONSE_DOCS.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Annotated
from typing import Any

from pydantic import Field

from core.api.schemas import ErrorSchema
from core.api.schemas import ValidationErrorSchema

# 401/403/406/429 can answer any authenticated operation (core.api.exceptions,
# core.api.negotiation and the throttles).
READ_ERRORS: dict[int, Any] = {
    401: ErrorSchema,
    403: ErrorSchema,
    406: ErrorSchema,
    429: ErrorSchema,
}
LOOKUP_ERRORS: dict[int, Any] = {**READ_ERRORS, 404: ErrorSchema}
# A malformed page or page_size is a 400 with fields; a page past the last one
# is a 404 (docs/api.md#pagination).
LIST_ERRORS: dict[int, Any] = {**LOOKUP_ERRORS, 400: ValidationErrorSchema}


def _one_of(schema: dict[str, Any]) -> None:
    # Field json_schema_extra callable (Pydantic "JSON Schema" docs): the two
    # bodies are alternatives, as the published contract says (oneOf, not anyOf).
    if "anyOf" in schema:
        schema["oneOf"] = schema.pop("anyOf")


BadRequestBody = Annotated[
    ValidationErrorSchema | ErrorSchema, Field(json_schema_extra=_one_of)
]
WRITE_ERRORS: dict[int, Any] = {
    **READ_ERRORS,
    400: BadRequestBody,
    415: ErrorSchema,
}

# A request that clashes with the stored data: a unique value already used
# (already_exists), or a delete that PROTECT foreign keys refuse (protected).
# Add it to the operations that can (docs/api.md#duplicates, ADR 0023).
CONFLICT_ERRORS: dict[int, Any] = {409: ErrorSchema}

RESPONSE_DOCS: dict[str, dict[str, Any]] = {
    "400": {
        "description": (
            "Validation error (`fields` present), or a request body that cannot "
            "be parsed (no `fields`)."
        )
    },
    "401": {
        "description": "Not authenticated.",
        "headers": {
            "WWW-Authenticate": {
                "schema": {"type": "string"},
                "description": "Authentication scheme (`X-Session-Token`).",
            }
        },
    },
    "403": {
        "description": (
            "Permission denied, or CSRF failed on a cookie-authenticated write. "
            "Hidden objects are 404, not 403."
        )
    },
    "404": {"description": "Not found, not visible, or a page past the last one."},
    "406": {"description": "The Accept header cannot be satisfied."},
    "409": {
        "description": (
            "Conflicts with the stored data: a unique value already used, or "
            "dependent records that protect the resource."
        )
    },
    "415": {"description": "The request Content-Type is not accepted."},
    "429": {
        "description": "Throttled.",
        "headers": {
            "Retry-After": {
                "schema": {"type": "integer"},
                "description": "Seconds until the client may retry.",
            }
        },
    },
}


# A 400 whose only body is ValidationErrorSchema (LIST_ERRORS: query parameters,
# no request body to parse).
_VALIDATION_ONLY_400 = {"description": "Validation error; `fields` lists each error."}


def response_docs(errors: dict[int, Any]) -> dict[str, Any]:
    """Operation openapi_extra (guide/operations/#openapi_extra), no schema rewrite."""
    return {
        "responses": {
            code: _VALIDATION_ONLY_400
            if code == HTTPStatus.BAD_REQUEST and schema is ValidationErrorSchema
            else RESPONSE_DOCS[str(code)]
            for code, schema in errors.items()
            if str(code) in RESPONSE_DOCS
        }
    }


def download_docs(media_type: str, errors: dict[int, Any]) -> dict[str, Any]:
    """openapi_extra for a download operation (docs/files.md#endpoint).

    The 200 is the file in media_type, which ApiContractMiddleware also
    negotiates Accept against; the 302 is the redirect to a signed URL when
    the file is on object storage. Declare both: response={200: None,
    302: None, **errors}.
    """
    docs = response_docs(errors)
    docs["responses"][HTTPStatus.OK] = {
        "description": "The file.",
        "content": {media_type: {"schema": {"type": "string", "format": "binary"}}},
    }
    docs["responses"][HTTPStatus.FOUND] = {
        "description": "A redirect to a signed URL of the file, on object storage.",
        "headers": {
            "Location": {
                "schema": {"type": "string", "format": "uri"},
                "description": "The signed URL.",
            }
        },
    }
    return docs
