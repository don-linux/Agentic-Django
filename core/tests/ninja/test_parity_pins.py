"""Pins for the DRF parity branches restored after the Ninja migration.

NUL characters, the empty PATCH body, multipart 415, DoesNotExist and bare
PermissionDenied, Throttled without a wait, the page_size fallback, the
previous link to the first page, the cached count of a plain list, OPTIONS
and the ScopedThrottle cache key. Each one answered like DRF before; nothing
else exercised them.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest  # noqa: TC002  # runtime: Ninja signature
from ninja import Router
from ninja.errors import Throttled
from ninja.testing import TestClient
from ninja.throttling import BaseThrottle

from apps.users.api.views import UserOut
from apps.users.models import User
from core.api.parsers import JSONParser
from core.api.throttling import ScopedThrottle
from core.tests.ninja.pagination_views import _create_users
from core.tests.ninja.pagination_views import _list
from core.tests.ninja.pagination_views import cached_count_api
from core.tests.ninja.pagination_views import ordered_api
from core.tests.ninja.support import make_api

if TYPE_CHECKING:
    from django.test import Client

ME_URL = "/api/v1/users/me/"


@pytest.mark.django_db
def test_anonymous_options_is_401_with_allow(client: Client) -> None:
    response = client.options(ME_URL)
    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response["WWW-Authenticate"] == "X-Session-Token"
    assert response["Allow"] == "GET, PATCH, HEAD, OPTIONS"


@pytest.mark.django_db
def test_authenticated_options_is_drf_metadata(user_client: Client) -> None:
    response = user_client.options(ME_URL)
    assert response.status_code == HTTPStatus.OK
    assert response["Allow"] == "GET, PATCH, HEAD, OPTIONS"
    body = response.json()
    assert set(body) == {"name", "description", "renders", "parses"}
    assert body["renders"] == body["parses"] == ["application/json"]


def test_scoped_throttle_cache_key_names_the_scope(rf) -> None:
    throttle = ScopedThrottle("1/min", scope="uploads")
    request = rf.get("/", REMOTE_ADDR="203.0.113.9")
    request.user = None
    assert throttle.get_cache_key(request) == "throttle_uploads_203.0.113.9"


@pytest.mark.django_db
@pytest.mark.parametrize("target", ["me", "detail"])
def test_an_empty_patch_body_is_a_no_op(
    user: User, user_client: Client, target: str
) -> None:
    path = ME_URL if target == "me" else f"/api/v1/users/{user.public_id}/"
    before = user.name
    response = user_client.patch(path, "", content_type="application/json")
    assert response.status_code == HTTPStatus.OK
    user.refresh_from_db()
    assert user.name == before


@pytest.mark.django_db
def test_nul_characters_are_a_field_error(user_client: Client) -> None:
    response = user_client.patch(
        ME_URL, {"name": "a\x00b"}, content_type="application/json"
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["fields"]["name"] == [
        {
            "code": "null_characters_not_allowed",
            "detail": "Null characters are not allowed.",
        }
    ]


@pytest.mark.django_db
def test_multipart_is_415_not_400(user_client: Client) -> None:
    response = user_client.patch(ME_URL, data={"name": "Ada"})
    assert response.status_code == HTTPStatus.UNSUPPORTED_MEDIA_TYPE
    assert response.json()["code"] == "unsupported_media_type"


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_parser_rejects_non_finite_numbers(rf, value: str) -> None:
    request = rf.post("/", data=f'{{"n": {value}}}', content_type="application/json")
    with pytest.raises(ValueError, match="not JSON compliant"):
        JSONParser().parse_body(request)


def _raising_api() -> TestClient:
    api = make_api(throttle=[])
    router = Router()

    @router.get("/missing/", auth=None)
    def missing(request: HttpRequest) -> None:
        User.objects.get(pk=0)

    @router.get("/denied/", auth=None)
    def denied(request: HttpRequest) -> None:
        raise PermissionDenied

    @router.get("/throttled/", auth=None)
    def throttled(request: HttpRequest) -> None:
        raise Throttled(wait=None)

    @router.get("/throttled-for-three/", auth=None)
    def throttled_for_three(request: HttpRequest) -> None:
        raise Throttled(wait=3)

    api.add_router("/", router)
    return TestClient(api)


class _RefusingThrottle(BaseThrottle):
    """Ninja's public custom throttle: refuse, and ask for `seconds` (a float)."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds

    def allow_request(self, request: HttpRequest) -> bool:
        return False

    def wait(self) -> float:
        return self.seconds


def _refused_after(seconds: float) -> TestClient:
    api = make_api(throttle=[_RefusingThrottle(seconds)])
    router = Router()

    @router.get("/refused/", auth=None)
    def refused(request: HttpRequest) -> None:
        return None

    api.add_router("/", router)
    return TestClient(api)


@pytest.mark.django_db
def test_does_not_exist_is_404_not_500() -> None:
    response = _raising_api().get("/missing/")
    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json() == {"code": "not_found", "detail": "Not found."}


def test_bare_permission_denied_uses_the_drf_detail() -> None:
    response = _raising_api().get("/denied/")
    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.json()["detail"] == (
        "You do not have permission to perform this action."
    )


def test_throttled_without_wait_has_no_retry_after() -> None:
    response = _raising_api().get("/throttled/")
    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert response.json() == {"code": "throttled", "detail": "Request was throttled."}
    assert not response.has_header("Retry-After")


def test_a_raised_throttled_sets_retry_after_from_its_wait() -> None:
    # Operation._check_throttles only sets Retry-After for a throttle it runs.
    response = _raising_api().get("/throttled-for-three/")
    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert response["Retry-After"] == "3"
    assert response.json() == {
        "code": "throttled",
        "detail": "Request was throttled. Expected available in 3 seconds.",
    }


@pytest.mark.parametrize(
    ("wait", "seconds", "detail"),
    [
        (1.2, "2", "Request was throttled. Expected available in 2 seconds."),
        (0.4, "1", "Request was throttled. Expected available in 1 second."),
    ],
    ids=["rounded-up-plural", "singular"],
)
def test_a_fractional_wait_is_rounded_up_like_drf(
    wait: float, seconds: str, detail: str
) -> None:
    # DRF 3.18.3 Throttled: math.ceil(wait) and ngettext "second"/"seconds".
    response = _refused_after(wait).get("/refused/")
    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert response["Retry-After"] == seconds
    assert response.json() == {"code": "throttled", "detail": detail}


@pytest.mark.django_db
@pytest.mark.parametrize("raw", ["abc", "0", "-1"])
def test_a_bad_page_size_falls_back_to_the_default(raw: str) -> None:
    users = 3
    _create_users(users)
    response = _list(ordered_api(), f"?page_size={raw}")
    assert response.status_code == HTTPStatus.OK
    assert len(response.json()["results"]) == users


@pytest.mark.django_db
def test_an_empty_page_parameter_is_the_first_page() -> None:
    # DRF 3.18.3 PageNumberPagination: `query_params.get(page_query_param) or 1`.
    _create_users(2)
    empty = _list(ordered_api(), "?page=&page_size=1").json()
    first = _list(ordered_api(), "?page=1&page_size=1").json()
    assert empty["results"] == first["results"]
    assert empty["previous"] is None
    assert empty["next"] is not None


@pytest.mark.django_db
def test_the_previous_link_to_the_first_page_has_no_page_parameter() -> None:
    # DRF 3.18.3 PageNumberPagination.get_previous_link: remove_query_param for
    # page 1, replace_query_param otherwise. ninja.testing's request URL has no
    # query string, so only the page parameter shows here.
    _create_users(3)
    second = _list(ordered_api(), "?page=2&page_size=1").json()
    third = _list(ordered_api(), "?page=3&page_size=1").json()
    assert second["previous"] == "http://testlocation/"
    assert third["previous"] == "http://testlocation/?page=2"


@pytest.mark.django_db
def test_cached_count_counts_a_plain_list() -> None:
    users = 2
    _create_users(users)
    api = cached_count_api(lambda: list(User.objects.order_by("pk")))
    response = _list(api)
    assert response.status_code == HTTPStatus.OK
    assert response.json()["count"] == users


@pytest.mark.django_db
def test_user_url_without_a_request_is_relative(user: User) -> None:
    assert UserOut.resolve_url(user) == f"/api/v1/users/{user.public_id}/"
