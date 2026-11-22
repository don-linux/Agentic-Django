"""User API: list, detail, and me (GET + partial PATCH)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Annotated
from typing import Any
from uuid import UUID
from weakref import WeakKeyDictionary

from django.http import Http404
from django.http import HttpRequest
from django.urls import reverse
from ninja import BodyEx
from ninja import P
from ninja import PathEx
from ninja import Router
from ninja.pagination import paginate
from pydantic import AfterValidator
from pydantic import Field
from pydantic import ValidationInfo
from pydantic import WithJsonSchema
from pydantic import field_validator
from pydantic import model_validator
from pydantic_core import PydanticCustomError

from apps.users.models import User
from apps.users.selectors import user_list
from apps.users.services import user_update
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


# DRF CharField's codes and messages for the same input (ADR 0006).
NULL_CODE = "null"
NULL_DETAIL = "This field may not be null."
NUL_CHAR_CODE = "null_characters_not_allowed"
NUL_CHAR_DETAIL = "Null characters are not allowed."


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
        # reverse() keeps the script prefix (SCRIPT_NAME, FORCE_SCRIPT_NAME), as
        # the DRF hyperlinked field did; build_absolute_uri() only adds the host.
        path = reverse("api:user-detail", kwargs={"id": obj.public_id})
        if request is not None:
            return request.build_absolute_uri(path)
        return path


class UserUpdateIn(InputSchema):
    # Optional but not nullable: null is a 400 "null" (coerce_and_reject_null),
    # so the published request schema must not offer it. WithJsonSchema only
    # changes the schema; SkipJsonSchema[None] would turn the Optional into a
    # union and nest its errors under name.str / name.none.
    name: Annotated[
        str | None, WithJsonSchema({"type": "string", "maxLength": 255})
    ] = Field(default=None, max_length=255)

    @model_validator(mode="before")
    @classmethod
    def drop_read_only(cls, data: Any) -> Any:
        # A GET body sent back includes id and url; those stay out of the write.
        if isinstance(data, dict):
            data = dict(data)
            data.pop("id", None)
            data.pop("url", None)
        return data

    @field_validator("name", mode="before")
    @classmethod
    def coerce_and_reject_null(cls, value: Any) -> Any:
        # DRF CharField coerced numbers to strings; booleans stay invalid.
        if value is None:
            raise PydanticCustomError(NULL_CODE, NULL_DETAIL)
        if isinstance(value, bool):
            return value
        if isinstance(value, int | float):
            return str(value)
        return value

    @field_validator("name", mode="after")
    @classmethod
    def reject_null_characters(cls, value: str | None) -> str | None:
        # DRF CharField's ProhibitNullCharactersValidator; Postgres refuses NUL.
        if value is not None and "\x00" in value:
            raise PydanticCustomError(NUL_CHAR_CODE, NUL_CHAR_DETAIL)
        return value


# The user a request already looked up: the path validator runs before the
# view, so a detail PATCH looks the object up once. Weak keys: an entry lives
# only as long as its request, and no other request can read it.
_VISIBLE_USERS: WeakKeyDictionary[HttpRequest, User] = WeakKeyDictionary()


def _visible_user(request: HttpRequest, public_id: UUID) -> User:
    cached = _VISIBLE_USERS.get(request)
    if cached is not None and cached.public_id == public_id:
        return cached
    current = request.user
    if (
        not current.is_authenticated
        or current.pk is None
        or not isinstance(current, User)
    ):
        msg = "Not found."
        raise Http404(msg)
    user = user_list(user=current).filter(public_id=public_id).first()
    if user is None:
        msg = "Not found."
        raise Http404(msg)
    _VISIBLE_USERS[request] = user
    return user


def _current_user(request: HttpRequest) -> User:
    # API_AUTH only lets authenticated users through; narrow AnonymousUser away.
    user = request.user
    if not isinstance(user, User):
        msg = "Not found."
        raise Http404(msg)
    return user


def _validate_visible_id(value: UUID, info: ValidationInfo) -> UUID:
    # Ninja passes {"request": request} to Pydantic validation. A public
    # AfterValidator on a path argument runs before the body is parsed.
    request = (info.context or {}).get("request")
    if request is not None:
        _visible_user(request, value)
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
VisibleUserId = Annotated[PublicUserId, AfterValidator(_validate_visible_id)]
# The contract names the path parameter id (/api/v1/users/{id}/); the alias
# keeps that name without an argument that shadows the builtin (guide/path-params/:
# Path accepts alias). PathEx is the type-checker spelling of Path[T, P(...)].
UserIdPath = PathEx[PublicUserId, P(alias="id")]
VisibleUserIdPath = PathEx[VisibleUserId, P(alias="id")]

# An empty PATCH body is no change (DRF parity). BodyEx, exported in ninja.__all__,
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
    current = request.user
    if (
        not current.is_authenticated
        or current.pk is None
        or not isinstance(current, User)
    ):
        return User.objects.none()
    return user_list(user=current)


@router.get(
    "/me/",
    response={200: UserOut, **READ_ERRORS},
    openapi_extra=response_docs(READ_ERRORS),
    operation_id="users_me_retrieve",
    url_name="user-me",
    summary="Retrieve the current user",
)
def users_me_retrieve(request: HttpRequest) -> User:
    return _current_user(request)


@router.patch(
    "/me/",
    response={200: UserOut, **WRITE_ERRORS},
    openapi_extra=response_docs(WRITE_ERRORS),
    operation_id="users_me_partial_update",
    url_name="user-me",
    summary="Partially update the current user",
)
def users_me_partial_update(request: HttpRequest, body: UserPatchBody) -> User:
    return _apply_update(_current_user(request), body.model_dump(exclude_unset=True))


@router.get(
    "/{public_id:id}/",
    response={200: UserOut, **LOOKUP_ERRORS},
    openapi_extra=response_docs(LOOKUP_ERRORS),
    operation_id="users_retrieve",
    url_name="user-detail",
    summary="Retrieve a user",
)
def users_retrieve(request: HttpRequest, user_id: UserIdPath) -> User:
    return _visible_user(request, user_id)


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
    user_id: VisibleUserIdPath,
    body: UserPatchBody,
) -> User:
    user = _visible_user(request, user_id)
    return _apply_update(user, body.model_dump(exclude_unset=True))
