"""API authentication: allauth X-Session-Token first, then Django session."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Any
from typing import override

from allauth.headless.contrib.ninja.security import (
    XSessionTokenAuth as AllauthXSessionTokenAuth,
)
from django.contrib.auth.base_user import AbstractBaseUser
from django.http import Http404
from ninja.errors import AuthenticationError
from ninja.errors import AuthorizationError
from ninja.security import SessionAuth

from core.errors import NOT_AUTHENTICATED_CODE
from core.errors import PERMISSION_DENIED_CODE
from core.errors import json_error

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.http import HttpRequest
    from django.http import HttpResponse

# The X-Session-Token first: Ninja stops at the first auth that returns a user
# (ninja/operation.py, _run_authentication), so a token request that also
# carries a session cookie never reaches django_auth's CSRF check
# (vitalik/django-ninja#1464, docs/typing.md#django-ninja-mechanisms).


class XSessionTokenAuth(AllauthXSessionTokenAuth):
    """allauth's X-Session-Token auth for Ninja, published as an apiKey header.

    Ninja publishes the openapi_* attributes as the security scheme, named
    after the class (ninja/security/base.py, ninja/openapi/schema.py).
    """

    openapi_type = "apiKey"
    openapi_in = "header"
    openapi_name = "X-Session-Token"
    openapi_description = "X-Session-Token authentication"


class cookieAuth(SessionAuth):  # noqa: N801
    """Staff session for the admin and Swagger.

    Lower camel case: the class name is the security scheme's name in the
    contract (docs/openapi.json).
    """

    openapi_type = "apiKey"
    openapi_in = "cookie"
    # No openapi_name: APIKeyBase.__init__ sets it from param_name, which is
    # settings.SESSION_COOKIE_NAME (ninja/security/apikey.py, session.py).
    openapi_description = (
        "Staff session for the admin and Swagger only. The cookie name is "
        "SESSION_COOKIE_NAME (`sessionid` locally, `__Secure-sessionid` in "
        "production). PATCH, POST, PUT and DELETE also require CSRF."
    )

    @override
    def __call__(self, request: HttpRequest) -> Any:
        # Ninja checks CSRF before authenticate() runs, so a request without an
        # authenticated session gets no user here first: a write without valid
        # credentials is a 401, not a CSRF 403 (vitalik/django-ninja#1464). With
        # one, SessionAuth runs as Ninja ships it: cookie, CSRF and csrf_exempt.
        if not request.user.is_authenticated:
            return None
        return super().__call__(request)


x_session_token_auth = XSessionTokenAuth()
django_auth = cookieAuth()
API_AUTH: list[XSessionTokenAuth | cookieAuth] = [x_session_token_auth, django_auth]


def staff_docs_required(
    view: Callable[..., HttpResponse],
) -> Callable[..., HttpResponse]:
    """
    The NinjaAPI docs_decorator: staff-only schema and docs, with the API's JSON
    401 and 403 (https://django-ninja.dev/guide/openapi/#protecting-the-docs).

    staff_member_required would redirect to the admin login. The
    X-Session-Token comes first, then the session, in API_AUTH's order: a
    staff token is staff even next to another user's session cookie. The token
    goes through the API's own x_session_token_auth, which sets request.user.
    """

    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        user = x_session_token_auth(request)
        if user is None and getattr(request, "user", None) is not None:
            user = request.user if request.user.is_authenticated else None
        if user is None:
            # The texts of Ninja's own AuthenticationError and AuthorizationError.
            response = json_error(
                NOT_AUTHENTICATED_CODE,
                AuthenticationError().message,
                HTTPStatus.UNAUTHORIZED,
            )
            response["WWW-Authenticate"] = "X-Session-Token"
            return response
        if not getattr(user, "is_staff", False):
            return json_error(
                PERMISSION_DENIED_CODE,
                AuthorizationError().message,
                HTTPStatus.FORBIDDEN,
            )
        return view(request, *args, **kwargs)

    return wrapped


def request_user[U: AbstractBaseUser](request: HttpRequest, model: type[U]) -> U:
    """The authenticated caller of an operation, as the project's user model.

    API_AUTH refuses an anonymous request with 401 before the view runs, but
    request.user is still typed as the user model or AnonymousUser. This
    narrows it once, without cast(): views call request_user(request, User).
    An anonymous caller of an operation with auth=None is a 404, like an
    object the caller cannot see.
    """
    user = request.user
    if not isinstance(user, model):
        raise Http404
    return user
