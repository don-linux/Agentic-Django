"""
The API checked against its own OpenAPI contract, with generated requests.

schemathesis reads the schema the API publishes at /api/v1/schema/, which
scripts/check.sh keeps identical to docs/openapi.json. It sends every operation
positive and negative requests through the WSGI application in this process.
Each response must match the contract:

- no server error;
- a declared status code, content type, headers and body;
- schema-violating input rejected;
- authentication enforced.

A new endpoint is covered as soon as it is in the schema. A failure prints the
curl command that reproduces it. See docs/api.md#contract-conformance and ADR 0016.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest
import schemathesis
from django.core.wsgi import get_wsgi_application
from hypothesis import HealthCheck
from hypothesis import settings as hypothesis_settings

from apps.users.models import User
from core.tests.session_tokens import session_token

if TYPE_CHECKING:
    from pytest_django.fixtures import Settings
    from schemathesis import Case
    from schemathesis import HookContext
    from schemathesis import Response
    from schemathesis.core.failures import Failure
    from schemathesis.schemas import BaseSchema

CALLER = "conformance@example.com"
# Generated requests per operation, on top of the edge cases schemathesis always
# sends. They are the same on every run until Hypothesis or schemathesis changes.
EXAMPLES_PER_OPERATION = 50
# negative_data_rejection: the API accepted a request the schema calls invalid.
ACCEPTED_INVALID_INPUT = "ST201"
INTEGER = re.compile(r"[+-]?\d+")


def _bad_page_size(case: Case) -> bool:
    # A page_size that is not an integer falls back to the default page size, as
    # in DRF (core/tests/ninja/test_parity_pins.py,
    # test_a_bad_page_size_falls_back_to_the_default).
    page_size = (case.query or {}).get("page_size")
    if case.operation.label != "GET /api/v1/users/" or page_size is None:
        return False
    if isinstance(page_size, int) and not isinstance(page_size, bool):
        return False
    return not (isinstance(page_size, str) and INTEGER.fullmatch(page_size))


def _lenient_user_update(case: Case) -> bool:
    # Two DRF-parity behaviors of a user PATCH body:
    # - a number for name is stored as text (core/tests/http/test_negative_paths.py,
    #   test_an_integer_name_is_coerced_to_a_string);
    # - the read-only id and url of a GET body sent back are ignored
    #   (apps/users/tests/http/test_views.py, test_patch_accepts_a_get_body_sent_back).
    body = case.body
    if case.method.upper() != "PATCH" or not isinstance(body, dict):
        return False
    name = body.get("name")
    numeric_name = isinstance(name, int | float) and not isinstance(name, bool)
    return numeric_name or not {"id", "url"}.isdisjoint(body)


def filter_failure(
    context: HookContext, failure: Failure, case: Case, response: Response
) -> bool:
    """Keep every failure except the invalid input the API accepts on purpose."""
    if failure.code != ACCEPTED_INVALID_INPUT:
        return True
    return not (_bad_page_size(case) or _lenient_user_update(case))


@pytest.fixture
def caller_token(transactional_db: None) -> str:
    # transactional_db: the WSGI handler sends request_started, which closes the
    # connection of a test that runs inside a transaction
    # (apps/users/tests/ninja/test_url_script_prefix.py does the same).
    return session_token(CALLER)


@pytest.fixture
def api_schema(caller_token: str, settings: Settings) -> BaseSchema:
    staff_token = session_token("conformance-staff@example.com", is_staff=True)
    own_id = str(User.objects.get(email=CALLER).public_id)
    # schemathesis sends its requests with the Host header "localhost".
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, "localhost"]
    config = schemathesis.Config.from_dict(
        {
            "generation": {
                # The test sends a real session token; generated credentials would
                # only be noise. ignored_auth still probes without it.
                "with-security-parameters": False,
            },
            # Half of the positive requests to a detail route use the caller's own
            # user; the rest are unknown ids, which must be 404.
            "dictionaries": {"caller": {"values": [own_id]}},
            "parameters": {"path.id": {"dictionary": "caller", "probability": 0.5}},
        }
    )
    schema = schemathesis.openapi.from_wsgi(
        "/api/v1/schema/",
        get_wsgi_application(),
        config=config,
        headers={"X-Session-Token": staff_token, "Accept": "application/json"},
    )
    schema.hook(filter_failure)
    return schema


schema = schemathesis.pytest.from_fixture("api_schema")


@schema.parametrize()
@hypothesis_settings(
    max_examples=EXAMPLES_PER_OPERATION,
    derandomize=True,
    database=None,
    deadline=None,
    # caller_token is created once per operation, not once per example: the
    # requests share one user, as a real client does.
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_every_operation_matches_the_contract(case: Case, caller_token: str) -> None:
    case.call_and_validate(headers={"X-Session-Token": caller_token})
