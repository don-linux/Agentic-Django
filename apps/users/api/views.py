"""User API: list, detail, and me (GET + partial PATCH)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Annotated
from typing import Any
from typing import Final
from uuid import UUID

from django.http import HttpRequest
from django.urls import reverse
from ninja import BodyEx
from ninja import P
from ninja import PathEx
from ninja import Router
from ninja.pagination import paginate
from pydantic import Field
from pydantic import WithJsonSchema
from pydantic import field_validator
from pydantic_core import PydanticKnownError

from apps.users.models import User
from apps.users.selectors import user_list
from apps.users.selectors import user_visible
from apps.users.services import user_update
from core.api.auth import request_user
from core.api.pagination import LimitPageNumberPagination
from core.api.responses import LIST_ERRORS
from core.api.responses import LOOKUP_ERRORS
from core.api.responses import READ_ERRORS
from core.api.responses import WRITE_ERRORS
from core.api.responses import response_docs
from core.api.schemas import BaseSchema
from core.api.schemas import InputSchema

if TYPE_CHECKING:
    from django.db.models import QuerySet

router = Router(tags=["users"])


# Pydantic's own error for a None sent to a str field, raised as a known error
# so Pydantic writes the message
# (https://docs.pydantic.dev/latest/errors/validation_errors/#string_type).
_STRING_TYPE: Final = "string_type"


class UserOut(BaseSchema):
    id: UUID = Field(json_schema_extra={"readOnly": True})
    name: str = Field(max_length=255)
    url: str = Field(json_schema_extra={"format": "uri", "readOnly": True})

    @staticmethod
    def resolve_id(obj: User) -> UUID:
        return obj.public_id

    @staticmethod
    def resolve_url(obj: User, context: dict[str, Any] | None = None) -> str:
        request = (context or {}).get("request")
        # reverse() keeps the script prefix (SCRIPT_NAME, FORCE_SCRIPT_NAME);
        # build_absolute_uri() only adds the host.
        path = reverse("api:user-detail", kwargs={"id": obj.public_id})
        if request is not None:
            return request.build_absolute_uri(path)
        return path


class UserUpdateIn(InputSchema):
    # Optional but not nullable: an omitted name stays unset, and null gets the
    # string_type error Pydantic gives a str field (reject_null), so
    # the published request schema must not offer it. WithJsonSchema only
    # changes the schema; SkipJsonSchema[None] would turn the Optional into a
    # union and nest its errors under name.str / name.none. Pydantic's MISSING
    # default would say this natively, but Ninja 1.8.0 cannot publish it
    # (NinjaGenerateJsonSchema.default_schema encodes every non-None default):
    # vitalik/django-ninja#1655, tracked in docs/typing.md#tracked-upstream.
    name: Annotated[
        str | None, WithJsonSchema({"type": "string", "maxLength": 255})
    ] = Field(default=None, max_length=255)

    @field_validator("name", mode="before")
    @classmethod
    def reject_null(cls, value: object) -> object:
        if value is None:
            raise PydanticKnownError(_STRING_TYPE)
        return value


PublicUserId = Annotated[
    UUID,
    WithJsonSchema(
        {
            "type": "string",
            "pattern": (
                r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
            ),
        }
    ),
]
# The contract names the path parameter id (/api/v1/users/{id}/); the alias
# keeps that name without an argument that shadows the builtin (guide/path-params/:
# Path accepts alias). PathEx is the type-checker spelling of Path[T, P(...)].
UserIdPath = PathEx[PublicUserId, P(alias="id")]

# The body is optional, so an empty PATCH changes nothing, as an empty {} does
# (ADR 0021: requiring it is an oasdiff ERR). BodyEx, exported in ninja.__all__,
# is the type-checker spelling of Body[T, P(...)] (ninja/params/__init__.py,
# guide/body/).
UserPatchBody = BodyEx[UserUpdateIn, P(default_factory=UserUpdateIn)]


def _apply_update(user: User, data: dict[str, Any]) -> User:
    if "name" not in data:
        return user
    return user_update(user=user, name=data["name"])


@router.get(
    "/",
    response={200: list[UserOut], **LIST_ERRORS},
    openapi_extra=response_docs(LIST_ERRORS),
    operation_id="users_list",
    url_name="user-list",
    summary="List users",
)
@paginate(LimitPageNumberPagination)
def users_list(request: HttpRequest) -> QuerySet[User]:
    return user_list(user=request_user(request, User))


@router.get(
    "/me/",
    response={200: UserOut, **READ_ERRORS},
    openapi_extra=response_docs(READ_ERRORS),
    operation_id="users_me_retrieve",
    url_name="user-me",
    summary="Retrieve the current user",
)
def users_me_retrieve(request: HttpRequest) -> User:
    return request_user(request, User)


@router.patch(
    "/me/",
    response={200: UserOut, **WRITE_ERRORS},
    openapi_extra=response_docs(WRITE_ERRORS),
    operation_id="users_me_partial_update",
    url_name="user-me",
    summary="Partially update the current user",
)
def users_me_partial_update(request: HttpRequest, body: UserPatchBody) -> User:
    return _apply_update(
        request_user(request, User), body.model_dump(exclude_unset=True)
    )


@router.get(
    "/{public_id:id}/",
    response={200: UserOut, **LOOKUP_ERRORS},
    openapi_extra=response_docs(LOOKUP_ERRORS),
    operation_id="users_retrieve",
    url_name="user-detail",
    summary="Retrieve a user",
)
def users_retrieve(request: HttpRequest, user_id: UserIdPath) -> User:
    return user_visible(user=request_user(request, User), public_id=user_id)


@router.patch(
    "/{public_id:id}/",
    response={200: UserOut, **LOOKUP_ERRORS, **WRITE_ERRORS},
    openapi_extra=response_docs({**LOOKUP_ERRORS, **WRITE_ERRORS}),
    operation_id="users_partial_update",
    url_name="user-detail",
    summary="Partially update a user",
)
def users_partial_update(
    request: HttpRequest,
    user_id: UserIdPath,
    body: UserPatchBody,
) -> User:
    user = user_visible(user=request_user(request, User), public_id=user_id)
    return _apply_update(user, body.model_dump(exclude_unset=True))
