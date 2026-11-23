"""406 only answers a route that exists, after the 405 and OPTIONS.

Each route renders one media type (JSON, or HTML for the docs page), and the
Accept header is read whole by RFC 9110's rules (ADR 0020). A path that matches
no route is Django's 404 whatever the Accept says.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from django.test import Client

NOT_FOUND = {"code": "not_found", "detail": "Not Found"}
NOT_ACCEPTABLE = {
    "code": "not_acceptable",
    "detail": "Could not satisfy the request Accept header.",
}


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("path", "accept"),
    [
        ("/api/v1/nothing/", "text/html"),
        ("/api/v1/users/1/", "text/plain"),
        ("/api/v1/users/me/extra/", "application/xml"),
        # The docs are HTML and the schema has its own media types: a path
        # under them that is not the route is still a plain 404.
        ("/api/v1/docs/extra/", "application/json"),
        ("/api/v1/schema/extra/", "text/plain"),
    ],
)
def test_a_path_that_is_not_a_route_is_404_whatever_the_accept(
    client: Client, user_client: Client, path: str, accept: str
) -> None:
    for caller in (client, user_client):
        response = caller.get(path, headers={"Accept": accept})
        assert response.status_code == HTTPStatus.NOT_FOUND, path
        assert response.json() == NOT_FOUND, path


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["POST", "OPTIONS", "HEAD"])
def test_no_route_is_404_for_any_method_with_an_unsupported_accept(
    user_client: Client, method: str
) -> None:
    response = user_client.generic(
        method, "/api/v1/nothing/", headers={"Accept": "text/html"}
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("path", "accept"),
    [
        ("/api/v1/users/me/", "text/html"),
        ("/api/v1/users/", "application/xml"),
        ("/api/v1/docs/", "application/json"),
    ],
)
def test_a_route_that_exists_still_refuses_an_accept_it_cannot_produce(
    client: Client, user_client: Client, path: str, accept: str
) -> None:
    # The negative case: the 404 is for paths without a route, not a way around
    # the 406, which comes before authentication.
    for caller in (client, user_client):
        response = caller.get(path, headers={"Accept": accept})
        assert response.status_code == HTTPStatus.NOT_ACCEPTABLE, path
        assert response.json() == NOT_ACCEPTABLE, path


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/api/v1/users/me/", "/api/v1/users/"])
@pytest.mark.parametrize(
    ("method", "status"),
    [
        ("PUT", HTTPStatus.METHOD_NOT_ALLOWED),
        ("DELETE", HTTPStatus.METHOD_NOT_ALLOWED),
        ("OPTIONS", HTTPStatus.OK),
        ("GET", HTTPStatus.NOT_ACCEPTABLE),
        ("HEAD", HTTPStatus.NOT_ACCEPTABLE),
    ],
)
def test_the_405_and_options_come_before_the_406(
    client: Client, user_client: Client, path: str, method: str, status: int
) -> None:
    # As Django and Ninja dispatch: an unknown method is 405 before anything
    # else, and OPTIONS has no body to negotiate. Only a method that renders
    # the route's media type can be 406, also when anonymous.
    for caller in (client, user_client):
        response = caller.generic(method, path, headers={"Accept": "text/html"})
        assert response.status_code == status, (method, path)


@pytest.mark.django_db
def test_an_acceptable_accept_keeps_the_405_before_authentication(
    client: Client,
) -> None:
    # The negative case: with an Accept the route produces, the method check is
    # still next and comes before authentication (405, not 401).
    for accept in ("application/json", "*/*", "application/*"):
        response = client.put("/api/v1/users/me/", headers={"Accept": accept})
        assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED, accept


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path", ["/api/v1/users/me/", "/api/v1/users/", "/api/v1/docs/", "/api/v1/schema/"]
)
@pytest.mark.parametrize("accept", ["", " ", "*/json", "*/yaml", "*/html", "*/*json"])
def test_an_empty_accept_or_an_invalid_media_range_is_406(
    client: Client, admin_client: Client, path: str, accept: str
) -> None:
    # A present but empty Accept offers no media type, and "*/json" is not a
    # media range (RFC 9110 section 12.5.1): neither matches what the route
    # renders, so both are 406 before authentication and the staff check.
    for caller in (client, admin_client):
        response = caller.get(path, headers={"Accept": accept})
        assert response.status_code == HTTPStatus.NOT_ACCEPTABLE, (path, accept)
        assert response.json() == NOT_ACCEPTABLE, (path, accept)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("path", "accept"),
    [
        ("/api/v1/users/me/", None),
        ("/api/v1/users/me/", "*/*"),
        ("/api/v1/users/me/", "application/*"),
        ("/api/v1/users/me/", "*/json, application/json"),
        ("/api/v1/users/me/", ", application/json"),
        ("/api/v1/docs/", None),
        ("/api/v1/docs/", "text/*"),
        ("/api/v1/schema/", None),
        ("/api/v1/schema/", "*/yaml, */*"),
    ],
)
def test_no_accept_or_a_valid_range_is_not_406(
    admin_client: Client, path: str, accept: str | None
) -> None:
    # The negative case: no Accept header is */*, and a valid range next to an
    # invalid or empty member still matches.
    headers = {} if accept is None else {"Accept": accept}
    response = admin_client.get(path, headers=headers)
    assert response.status_code == HTTPStatus.OK, (path, accept)


@pytest.mark.django_db
@pytest.mark.parametrize("accept", ["", "*/json"])
def test_an_empty_or_invalid_accept_on_no_route_is_still_404(
    user_client: Client, accept: str
) -> None:
    # Only a route that exists negotiates: elsewhere it is Django's 404.
    response = user_client.get("/api/v1/nothing/", headers={"Accept": accept})
    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json() == NOT_FOUND


JSON = "application/json"
SCHEMA_TYPES = (
    "application/vnd.oai.openapi",
    "application/yaml",
    "application/vnd.oai.openapi+json",
    JSON,
)


def _members(count: int) -> list[str]:
    return [f"text/x-{i}" for i in range(count)]


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/api/v1/users/me/", "/api/v1/schema/"])
@pytest.mark.parametrize(
    "accept",
    [
        ", ".join([*_members(64), JSON]),
        ", , , " + ", ".join([*_members(80), JSON]),
        JSON + ";p=" + "a" * 300,
        "text/x-a;p=" + "b" * 9000 + ", " + JSON,
    ],
    ids=["65th-member", "after-empty-members", "long-member", "long-header"],
)
def test_every_member_of_a_long_accept_counts(
    admin_client: Client, path: str, accept: str
) -> None:
    # No member is skipped and nothing is cut: the server's own header limit
    # (gunicorn limit_request_field_size in production) bounds the work.
    response = admin_client.get(path, headers={"Accept": accept})
    assert response.status_code == HTTPStatus.OK, (path, accept[:40])


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("path", "accept"),
    [
        # RFC 9110 section 12.4.2: a weight of 0 means "not acceptable", and
        # the "q" parameter name is case-insensitive.
        ("/api/v1/users/me/", "application/json;q=0"),
        ("/api/v1/users/me/", "application/json; q=0.000"),
        ("/api/v1/users/me/", "application/json;Q=0."),
        ("/api/v1/users/me/", "application/*;q=0"),
        ("/api/v1/users/me/", "*/*;q=0"),
        ("/api/v1/users/me/", "text/html, application/json;q=0"),
        ("/api/v1/docs/", "text/html;q=0"),
        ("/api/v1/schema/", ", ".join(f"{media};q=0" for media in SCHEMA_TYPES)),
    ],
)
def test_a_range_with_q_0_is_not_acceptable(
    client: Client, admin_client: Client, path: str, accept: str
) -> None:
    for caller in (client, admin_client):
        response = caller.get(path, headers={"Accept": accept})
        assert response.status_code == HTTPStatus.NOT_ACCEPTABLE, (path, accept)
        assert response.json() == NOT_ACCEPTABLE, (path, accept)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("path", "accept", "content_type"),
    [
        # The negative case: any weight above 0 makes no difference here,
        # and a q=0 range leaves the other ranges of the header as they were.
        ("/api/v1/users/me/", "application/json;q=0.001", JSON),
        ("/api/v1/users/me/", "application/json;q=1", JSON),
        ("/api/v1/users/me/", "text/html;q=0, application/json", JSON),
        ("/api/v1/users/me/", "*/*;q=0, application/json", JSON),
        ("/api/v1/schema/", "application/yaml;q=0, application/json", JSON),
    ],
)
def test_a_weight_above_0_is_still_acceptable(
    admin_client: Client, path: str, accept: str, content_type: str
) -> None:
    response = admin_client.get(path, headers={"Accept": accept})
    assert response.status_code == HTTPStatus.OK, (path, accept)
    assert response["Content-Type"] == content_type, (path, accept)


@pytest.mark.django_db
def test_the_contract_holds_under_a_script_prefix(client: Client) -> None:
    # Served under a prefix (SCRIPT_NAME, or FORCE_SCRIPT_NAME behind a proxy),
    # request.path starts with it; the route is request.path_info, which URL
    # resolution uses too.
    refused = client.get(
        "/api/v1/users/me/", headers={"Accept": "text/html"}, SCRIPT_NAME="/app"
    )
    assert refused.status_code == HTTPStatus.NOT_ACCEPTABLE
    assert refused.json() == NOT_ACCEPTABLE
    not_allowed = client.put("/api/v1/users/me/", SCRIPT_NAME="/app")
    assert not_allowed.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert not_allowed.json() == {
        "code": "method_not_allowed",
        "detail": "Method not allowed",
    }
    assert not_allowed["Allow"] == "GET, PATCH, HEAD, OPTIONS"


@pytest.mark.django_db
def test_a_path_that_is_not_a_route_under_a_script_prefix_is_404(
    client: Client,
) -> None:
    # The negative case: under the prefix too, only a route negotiates.
    response = client.get(
        "/api/v1/nothing/", headers={"Accept": "text/html"}, SCRIPT_NAME="/app"
    )
    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json() == NOT_FOUND


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("path", "accept"),
    [
        # RFC 9110 section 12.5.1: "Media ranges can be overridden by more
        # specific media ranges or specific media types. If more than one media
        # range applies to a given type, the most specific reference has
        # precedence." So a q=0 on the media type wins over */* or type/*, in
        # any order.
        ("/api/v1/users/me/", "*/*, application/json;q=0"),
        ("/api/v1/users/me/", "application/json;q=0, */*"),
        ("/api/v1/users/me/", "application/*, application/json;q=0"),
        ("/api/v1/users/me/", "*/*, application/*;q=0"),
        ("/api/v1/docs/", "text/html;q=0, */*"),
        ("/api/v1/docs/", "*/*, text/*;q=0"),
        (
            "/api/v1/schema/",
            ", ".join(["*/*", *(f"{media};q=0" for media in SCHEMA_TYPES)]),
        ),
    ],
)
def test_the_most_specific_range_gives_the_weight(
    client: Client, admin_client: Client, path: str, accept: str
) -> None:
    for caller in (client, admin_client):
        response = caller.get(path, headers={"Accept": accept})
        assert response.status_code == HTTPStatus.NOT_ACCEPTABLE, (path, accept)
        assert response.json() == NOT_ACCEPTABLE, (path, accept)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("path", "accept", "content_type"),
    [
        # The negative case: a more specific range above 0 still accepts under
        # a q=0 on */* or on another type; an invalid range (*/json) gives no
        # weight; at one precedence a range above 0 accepts.
        ("/api/v1/users/me/", "*/*;q=0, application/json", JSON),
        ("/api/v1/users/me/", "text/*;q=0, */*", JSON),
        ("/api/v1/users/me/", "application/xml;q=0, */*", JSON),
        ("/api/v1/users/me/", "*/json;q=0, application/json", JSON),
        ("/api/v1/users/me/", "*/*, */json;q=0", JSON),
        ("/api/v1/users/me/", "application/json, application/json;q=0", JSON),
        ("/api/v1/docs/", "*/*;q=0, text/html", "text/html; charset=utf-8"),
        ("/api/v1/schema/", "*/*, application/yaml;q=0", JSON),
        ("/api/v1/schema/", "*/*;q=0, application/json", JSON),
    ],
)
def test_a_more_specific_range_above_0_still_accepts(
    admin_client: Client, path: str, accept: str, content_type: str
) -> None:
    response = admin_client.get(path, headers={"Accept": accept})
    assert response.status_code == HTTPStatus.OK, (path, accept)
    assert response["Content-Type"] == content_type, (path, accept)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("path", "accept", "content_type"),
    [
        # RFC 9110 section 12.5.1, table 5: a range with parameters applies to
        # the representation with those parameters. With q=0 it refuses only
        # that variant, as Django's MediaType.match: the media type without
        # them keeps the weight the other ranges give it.
        ("/api/v1/users/me/", "application/json, application/json;indent=2;q=0", JSON),
        ("/api/v1/users/me/", "*/*, application/json;charset=utf-8;q=0", JSON),
        ("/api/v1/users/me/", "*/*, application/*;charset=utf-8;q=0", JSON),
        ("/api/v1/users/me/", "application/json;indent=2;q=0, application/*", JSON),
        (
            "/api/v1/docs/",
            "text/html, text/html;level=1;q=0",
            "text/html; charset=utf-8",
        ),
        (
            "/api/v1/schema/",
            "application/json;charset=utf-8;q=0, application/json",
            JSON,
        ),
    ],
)
def test_a_q_0_with_parameters_refuses_only_that_variant(
    admin_client: Client, path: str, accept: str, content_type: str
) -> None:
    response = admin_client.get(path, headers={"Accept": accept})
    assert response.status_code == HTTPStatus.OK, (path, accept)
    assert response["Content-Type"] == content_type, (path, accept)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("path", "accept"),
    [
        # The negative case: a q=0 range without parameters still refuses, a
        # q=0 range with parameters alone leaves nothing acceptable, and an
        # invalid range still gives no weight.
        ("/api/v1/users/me/", "application/json;indent=2;q=0"),
        ("/api/v1/users/me/", "application/json;indent=2;q=0, application/json;q=0"),
        (
            "/api/v1/users/me/",
            "*/*, application/json;q=0, application/json;charset=utf-8;q=0",
        ),
        ("/api/v1/users/me/", "*/json, application/json;indent=2;q=0"),
        ("/api/v1/docs/", "text/html;level=1;q=0"),
        (
            "/api/v1/schema/",
            ", ".join(
                [
                    "application/json;indent=2;q=0",
                    *(f"{media};q=0" for media in SCHEMA_TYPES[:-1]),
                ]
            ),
        ),
    ],
)
def test_a_q_0_without_parameters_still_refuses(
    client: Client, admin_client: Client, path: str, accept: str
) -> None:
    for caller in (client, admin_client):
        response = caller.get(path, headers={"Accept": accept})
        assert response.status_code == HTTPStatus.NOT_ACCEPTABLE, (path, accept)
        assert response.json() == NOT_ACCEPTABLE, (path, accept)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("path", "accept", "content_type", "start"),
    [
        # The negative case: above 0 a range with parameters still covers the
        # media type.
        ("/api/v1/users/me/", "*/*;q=0, application/json;charset=utf-8", JSON, b"{"),
        ("/api/v1/schema/", "*/*;q=0, application/json;indent=2", JSON, b'{"openapi"'),
    ],
)
def test_a_range_with_parameters_above_0_still_chooses(
    admin_client: Client, path: str, accept: str, content_type: str, start: bytes
) -> None:
    response = admin_client.get(path, headers={"Accept": accept})
    assert response.status_code == HTTPStatus.OK, (path, accept)
    assert response["Content-Type"] == content_type, (path, accept)
    assert response.content.startswith(start), (path, accept)
