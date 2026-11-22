"""Error responses every operation declares, so docs/openapi.json lists them.

Spread them into `response=` (Ninja's documented way to declare several status
codes: https://django-ninja.dev/guides/responses/#multiple-response-schemas):

    @router.get("/", response={200: list[ThingOut], **LIST_ERRORS})
    @router.get("/{id}/", response={200: ThingOut, **LOOKUP_ERRORS})
    @router.patch("/{id}/", response={200: ThingOut, **LOOKUP_ERRORS, **WRITE_ERRORS})

The descriptions and the WWW-Authenticate / Retry-After headers are added by
the documented operation openapi_extra option, from RESPONSE_DOCS.
"""

from __future__ import annotations

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
# A page past the end, or an invalid page number, is a 404 (docs/api.md).
LIST_ERRORS: dict[int, Any] = LOOKUP_ERRORS


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

RESPONSE_DOCS: dict[str, dict[str, Any]] = {
    "400": {
        "description": (
            "Validation error (`fields` present) or parse error (no `fields`)."
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
    "404": {"description": "Not found, not visible, or an invalid page number."},
    "406": {"description": "The Accept header cannot be satisfied."},
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


def response_docs(errors: dict[int, Any]) -> dict[str, Any]:
    """Operation openapi_extra (guide/operations/#openapi_extra), no schema rewrite."""
    return {
        "responses": {
            code: RESPONSE_DOCS[str(code)]
            for code in errors
            if str(code) in RESPONSE_DOCS
        }
    }
