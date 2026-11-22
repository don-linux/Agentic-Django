"""Direct coverage of parsers, throttles and pagination helpers."""

from __future__ import annotations

from http import HTTPStatus

import pytest
from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.http import Http404
from django.test import RequestFactory
from ninja import Router
from ninja.errors import HttpError
from ninja.parser import Parser
from ninja.testing import TestClient

from apps.users.models import User
from core.api.exceptions import _ninja_validation_fields
from core.api.pagination import LimitPageNumberPagination
from core.api.pagination import _abs_url
from core.api.pagination import _parse_page
from core.api.pagination import _parse_page_size
from core.api.parsers import JSONParser
from core.api.throttling import ScopedThrottle
from core.tests.ninja.pagination_views import _list
from core.tests.ninja.pagination_views import cached_count_api
from core.tests.ninja.support import make_api


def test_parse_page_and_page_size_reject_values_below_one() -> None:
    size, cap = 20, 100
    with pytest.raises(Http404):
        _parse_page(0)
    with pytest.raises(Http404):
        _parse_page_size(0, default=size, maximum=cap)
    assert _parse_page(None) == 1
    assert _parse_page_size(None, default=size, maximum=cap) == size
    assert _parse_page_size(500, default=size, maximum=cap) == cap


def test_abs_url_includes_page_size_when_present() -> None:
    request = RequestFactory().get("/api/v1/users/", {"page_size": "10"})
    url = _abs_url(request, page=2, page_size=10, default_size=20)
    assert "page=2" in url
    assert "page_size=10" in url


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
    def boom(self, request):
        raise RequestDataTooBig

    monkeypatch.setattr(Parser, "parse_body", boom)
    with pytest.raises(RequestDataTooBig):
        JSONParser().parse_body(RequestFactory().post("/"))


def test_ninja_validation_fields_nests_and_maps_min_length() -> None:

    fields = _ninja_validation_fields(
        [
            {
                "loc": ("body", "address", "street"),
                "type": "string_too_short",
                "msg": "too short",
            }
        ]
    )
    assert fields["address"]["street"][0]["code"] == "min_length"


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
