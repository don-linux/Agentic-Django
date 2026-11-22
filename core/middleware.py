"""
Request middleware: the health probes, request ids and the /api/ conventions.

HealthCheckMiddleware runs before host and HTTPS checks and never calls
get_host(). See docs/ARCHITECTURE.md#deployment.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import uuid
from contextvars import ContextVar
from http import HTTPStatus
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import Error
from django.db import connection
from django.http import Http404
from django.http import HttpResponse
from django.http import HttpResponseBadRequest
from django.http import HttpResponseNotAllowed
from django.middleware.common import CommonMiddleware
from django.utils.cache import add_never_cache_headers
from django.utils.module_loading import import_string
from redis.exceptions import RedisError

from core.cache import probe

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.http import HttpRequest

logger = logging.getLogger(__name__)

_HEALTH_PATH = "/health/"
_LIVE_PATH = "/health/live/"
_OK = "ok\n"
_DATABASE_UNAVAILABLE = "database unavailable\n"
_CACHE_UNAVAILABLE = "cache unavailable\n"
_NO_STORE_HEADERS = {"Cache-Control": "no-store"}

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
# What request_id_var holds outside a request (a shell, a command, a worker).
NO_REQUEST_ID = "-"
# The current request id, read by core.logging.RequestIdFilter.
request_id_var: ContextVar[str] = ContextVar("request_id", default=NO_REQUEST_ID)


class HealthCheckMiddleware:
    """
    Answer the health probes before the rest of the stack.

    /health/live/ never touches the database or the cache. /health/ runs SELECT 1
    and a Valkey PING and answers 503 while either fails: that is what makes an
    outage of the fail-open cache visible. See docs/ARCHITECTURE.md#deployment.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.path not in (_HEALTH_PATH, _LIVE_PATH):
            return self.get_response(request)
        if request.method != "GET":
            # text/plain, like the successful probes; HttpResponseNotAllowed
            # defaults to text/html even with an empty body.
            return HttpResponseNotAllowed(
                ["GET"],
                content=b"",
                content_type="text/plain",
                headers=_NO_STORE_HEADERS,
            )
        if request.path == _LIVE_PATH:
            return _plain_text(_OK, HTTPStatus.OK)
        failure = _readiness_failure()
        if failure is not None:
            return _plain_text(failure, HTTPStatus.SERVICE_UNAVAILABLE)
        return _plain_text(_OK, HTTPStatus.OK)


def _plain_text(body: str, status: HTTPStatus) -> HttpResponse:
    return HttpResponse(
        body,
        status=status,
        content_type="text/plain",
        headers=_NO_STORE_HEADERS,
    )


def _readiness_failure() -> str | None:
    """Return the body for a 503, or None when the database and the cache answer."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Error:
        logger.warning("healthcheck database unavailable", exc_info=True)
        return _DATABASE_UNAVAILABLE
    try:
        probe()
    except RedisError, OSError:
        # The error names the host and port, never the password.
        logger.warning("healthcheck cache unavailable", exc_info=True)
        return _CACHE_UNAVAILABLE
    return None


class RequestIdMiddleware:
    """
    Give every request an id, for logs, the response, and error reports.

    A valid incoming X-Request-ID (for example nginx's $request_id) is kept so
    the proxy and Django logs share it; anything else is replaced.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = (
            incoming if _VALID_REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
        )
        request.request_id = request_id  # type: ignore[attr-defined]  # pyright: ignore[reportAttributeAccessIssue]
        token = request_id_var.set(request_id)
        try:
            _tag_error_reports(request_id)
            response = self.get_response(request)
            response[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            request_id_var.reset(token)


def _tag_error_reports(request_id: str) -> None:
    # Sentry is optional: tag events only when it is installed and initialized.
    sentry_sdk = sys.modules.get("sentry_sdk")
    if sentry_sdk is not None and sentry_sdk.is_initialized():
        sentry_sdk.set_tag("request_id", request_id)


class ApiCommonMiddleware(CommonMiddleware):
    """
    CommonMiddleware without APPEND_SLASH redirects for /api/ paths.

    A 301 would make clients repeat a POST as a GET; an API path without its final
    "/" is a 404. See docs/api.md#paths.
    """

    def should_redirect_with_slash(self, request: HttpRequest) -> bool:
        if request.path_info.startswith("/api/"):
            return False
        return super().should_redirect_with_slash(request)


class ApiDebugErrorMiddleware:
    """
    Keep JSON error bodies on /api/ and /_allauth/ even when DEBUG=True.

    Django replaces handler404/400/500 with HTML technical pages under DEBUG.
    That is useful for the admin, but Bruno and clients against a local server
    would otherwise see HTML for an unmatched UUID or an unknown API route.
    """

    _JSON_PREFIXES = ("/api/", "/_allauth/")
    _HANDLERS = {
        HTTPStatus.BAD_REQUEST: "core.errors.bad_request",
        HTTPStatus.FORBIDDEN: "core.errors.permission_denied",
        HTTPStatus.NOT_FOUND: "core.errors.page_not_found",
        HTTPStatus.INTERNAL_SERVER_ERROR: "core.errors.server_error",
    }

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        if not settings.DEBUG:
            return response
        if not request.path_info.startswith(self._JSON_PREFIXES):
            return response
        # A code outside the IANA registry (499, 299) is not an HTTPStatus member.
        if response.status_code not in self._HANDLERS:
            return response
        status = HTTPStatus(response.status_code)
        if response.get("Content-Type", "").startswith("application/json"):
            return response
        return self._json_error(request, status)

    def _json_error(self, request: HttpRequest, status: HTTPStatus) -> HttpResponse:
        handler = import_string(self._HANDLERS[status])
        if status is HTTPStatus.INTERNAL_SERVER_ERROR:
            return handler(request)
        exc: Exception
        if status is HTTPStatus.FORBIDDEN:
            exc = PermissionDenied()
        elif status is HTTPStatus.NOT_FOUND:
            exc = Http404()
        else:
            exc = Exception()
        return handler(request, exception=exc)


class ApiNoStoreMiddleware:
    """
    Mark /api/ responses as non-cacheable unless the view set Cache-Control.

    Shared caches do not treat X-Session-Token like Authorization.
    See docs/api.md#caching.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        if request.path_info.startswith("/api/") and not response.has_header(
            "Cache-Control",
        ):
            add_never_cache_headers(response)
        return response


class AllauthJsonDepthMiddleware:
    """
    Answer 400 to an allauth JSON body nested too deeply to decode.

    allauth parses /_allauth/ bodies itself and catches only malformed JSON;
    Python's RecursionError would be a 500. The 400 matches allauth's own answer
    to malformed JSON. See docs/api.md#out-of-scope-_allauth.
    """

    _PREFIX = "/_allauth/"

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if (
            request.path_info.startswith(self._PREFIX)
            and request.method != "GET"
            and request.body
        ):
            try:
                json.loads(request.body)
            except RecursionError:
                return HttpResponseBadRequest()
            except ValueError:
                pass  # Malformed JSON: allauth answers it.
        return self.get_response(request)
