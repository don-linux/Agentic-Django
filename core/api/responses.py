"""Error responses every operation declares, so docs/openapi.json lists them.

Spread a map into `response=` (Ninja's documented way to declare several
status codes: https://django-ninja.dev/guide/responses/#multiple-response-schemas),
and pass the same map to response_docs(), which the documented operation
openapi_extra option uses for the descriptions and the WWW-Authenticate and
Retry-After headers:

    @router.get(
        "/{public_id:id}/",
        response={200: ThingOut, **LOOKUP_ERRORS},
        openapi_extra=response_docs(LOOKUP_ERRORS),
    )

See docs/api.md#error-responses-in-openapi.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Annotated
from typing import Any

from pydantic import Field

from core.api.schemas import ErrorSchema
from core.api.schemas import ValidationErrorSchema

# 406/429 can answer any operation, a public one (auth=None) included
# (core.api.negotiation and the throttles).
PUBLIC_ERRORS: dict[int, Any] = {406: ErrorSchema, 429: ErrorSchema}
# Plus 401/403 on an authenticated operation (core.api.exceptions).
READ_ERRORS: dict[int, Any] = {401: ErrorSchema, 403: ErrorSchema, **PUBLIC_ERRORS}
LOOKUP_ERRORS: dict[int, Any] = {**READ_ERRORS, 404: ErrorSchema}
# A paginated list: a malformed page, page_size or cursor parameter is a 400
# with fields, and a page past the last one, or a cursor the server did not
# issue, is a 404 (docs/api.md#pagination). A public list spreads
# {**PUBLIC_ERRORS, **PAGE_ERRORS}.
PAGE_ERRORS: dict[int, Any] = {404: ErrorSchema, 400: ValidationErrorSchema}
LIST_ERRORS: dict[int, Any] = {**READ_ERRORS, **PAGE_ERRORS}


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

# A request that breaks a business rule (ApplicationError, ConflictError), a
# unique value already used (already_exists), or a delete that PROTECT foreign
# keys refuse (protected). Add it to the operations that can
# (docs/api.md#domain-errors, ADR 0023).
CONFLICT_ERRORS: dict[int, Any] = {409: ErrorSchema}
# A deprecated operation past its sunset date (core.api.deprecation).
GONE_ERRORS: dict[int, Any] = {410: ErrorSchema}
# A write whose If-Match names a version that is no longer current
# (core.concurrency, core.api.schemas.IfMatch).
PRECONDITION_ERRORS: dict[int, Any] = {412: ErrorSchema}

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
    "404": {"description": "Not found, or not visible to the caller."},
    "406": {"description": "The Accept header cannot be satisfied."},
    "409": {
        "description": (
            "Conflicts with the stored data: a business rule, a unique value "
            "already used, or dependent records that protect the resource."
        )
    },
    "410": {"description": "Gone: the operation was removed after its sunset date."},
    "412": {
        "description": (
            "Precondition failed: If-Match does not name the current version."
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


# A list's 400 has only ValidationErrorSchema (PAGE_ERRORS: query parameters,
# no request body to parse), and its 404 may be a page.
_LIST_DOCS: dict[int, dict[str, Any]] = {
    400: {"description": "Validation error; `fields` lists each error."},
    404: {
        "description": (
            "Not found or not visible, a page past the last one, or a cursor "
            "the server did not issue."
        )
    },
}
_ETAG_HEADER = {
    "headers": {
        "ETag": {
            "schema": {"type": "string"},
            "description": "The version to send back in If-Match.",
        }
    }
}


def response_docs(errors: dict[int, Any], *, etag: bool = False) -> dict[str, Any]:
    """Operation openapi_extra (guide/operations/#openapi_extra), no schema rewrite.

    etag=True documents the ETag header of the 200 (core.concurrency).
    """
    is_list = errors.get(HTTPStatus.BAD_REQUEST) is ValidationErrorSchema
    responses: dict[int, Any] = {
        code: _LIST_DOCS[code]
        if is_list and code in _LIST_DOCS
        else RESPONSE_DOCS[str(code)]
        for code in errors
        if str(code) in RESPONSE_DOCS
    }
    if etag:
        # Ninja deep-merges openapi_extra: the 200 keeps its body.
        responses[HTTPStatus.OK] = _ETAG_HEADER
    return {"responses": responses}


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
