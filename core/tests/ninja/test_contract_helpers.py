"""Direct coverage of parsers, throttles and pagination helpers."""

from __future__ import annotations

import re
from http import HTTPStatus

import pytest
from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.http import HttpRequest
from django.test import RequestFactory
from ninja import Router
from ninja.errors import HttpError
from ninja.testing import TestClient

from apps.users.models import User
from core.api.exceptions import _ninja_validation_fields
from core.api.pagination import LimitPageNumberPagination
from core.api.pagination import _positive_int
from core.api.pagination import _set_query_param
from core.api.parsers import JSONParser
from core.api.throttling import ScopedThrottle
from core.tests.ninja.pagination_views import _list
from core.tests.ninja.pagination_views import cached_count_api
from core.tests.ninja.support import make_api

REF_VALIDATION = "#/components/schemas/ValidationErrorSchema"
REF_ERROR = "#/components/schemas/ErrorSchema"
REF_UPDATE = "#/components/schemas/UserUpdateIn"


def test_positive_int_is_drf_strict_and_capped() -> None:
    cutoff = 100
    for raw in ("0", "-1"):
        with pytest.raises(ValueError, match=f"^{re.escape(raw)}$"):
            _positive_int(raw, cutoff=cutoff)
    for raw in ("abc", "1.0", ""):
        with pytest.raises(ValueError, match=r"^invalid literal for int\(\)"):
            _positive_int(raw, cutoff=cutoff)
    assert _positive_int("500", cutoff=cutoff) == cutoff


def test_set_query_param_sorts_keeps_lists_and_removes() -> None:
    url = "http://testserver/api/v1/users/?foo=b&foo=a&page_size=1&page=2"
    assert _set_query_param(url, "page", "3") == (
        "http://testserver/api/v1/users/?foo=b&foo=a&page=3&page_size=1"
    )
    assert _set_query_param(url, "page", None) == (
        "http://testserver/api/v1/users/?foo=b&foo=a&page_size=1"
    )


def test_pagination_counts_a_plain_list() -> None:
    paginator = LimitPageNumberPagination()
    items = ["a", "b"]
    assert paginator.count_items(items) == len(items)


def test_scoped_throttle_takes_the_rate_of_its_scope() -> None:
    # Without a rate, Ninja's SimpleRateThrottle.get_rate() looks the scope up
    # in NINJA_DEFAULT_THROTTLE_RATES.
    throttle = ScopedThrottle(scope="user")
    assert throttle.rate == settings.NINJA_DEFAULT_THROTTLE_RATES["user"]


def test_json_parser_reraises_request_data_too_big(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(self):
        raise RequestDataTooBig

    monkeypatch.setattr(HttpRequest, "body", property(boom))
    with pytest.raises(RequestDataTooBig):
        JSONParser().parse_body(RequestFactory().post("/"))


def test_ninja_validation_fields_nests_and_keeps_the_pydantic_type() -> None:

    fields = _ninja_validation_fields(
        [
            {
                "loc": ("body", "body", "address", "street"),
                "type": "string_too_short",
                "msg": "too short",
            }
        ]
    )
    assert fields["address"]["street"][0] == {
        "code": "string_too_short",
        "detail": "too short",
    }


def test_http_error_codes_for_media_auth_and_generic() -> None:

    def _client(exc: HttpError) -> TestClient:
        api = make_api(throttle=[], auth=None)
        router = Router()

        @router.get("/err/", auth=None)
        def err(request):
            raise exc

        api.add_router("/", router)
        return TestClient(api)

    cases = [
        (HTTPStatus.BAD_REQUEST, "JSON parse error", "parse_error"),
        (HTTPStatus.BAD_REQUEST, "nope", "error"),
        (HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "nope", "unsupported_media_type"),
        (HTTPStatus.NOT_ACCEPTABLE, "nope", "not_acceptable"),
        (HTTPStatus.FORBIDDEN, "CSRF check Failed", "permission_denied"),
        (HTTPStatus.FORBIDDEN, "nope", "error"),
        (HTTPStatus.IM_A_TEAPOT, "brew", "error"),
    ]
    for status, message, code in cases:
        response = _client(HttpError(status, message)).get("/err/")
        assert response.status_code == status
        assert response.json() == {"code": code, "detail": message}
    unauthorized = _client(HttpError(HTTPStatus.UNAUTHORIZED, "nope"))
    response = unauthorized.get("/err/")
    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.headers.get("WWW-Authenticate") == "X-Session-Token"
    assert response.json()["code"] == "not_authenticated"


@pytest.mark.django_db
def test_empty_queryset_past_first_page_is_404() -> None:

    api = cached_count_api(User.objects.none)
    response = _list(api, "?page=2")
    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json() == {"code": "not_found", "detail": "Invalid page."}
    first = _list(api, "?page=1")
    assert first.status_code == HTTPStatus.OK
    assert first.json()["results"] == []
