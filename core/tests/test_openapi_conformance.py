"""
The API checked against its own OpenAPI contract, with generated requests.

schemathesis reads the schema the API publishes at /api/v1/schema/, which
scripts/check.sh keeps identical to docs/openapi.json. It sends every operation
positive and negative requests through the WSGI application in this process.
Each response must match the contract:

- no server error;
- a declared status code, content type, headers and body;
- schema-violating input rejected, and schema-valid input accepted;
- authentication enforced.

Every check runs and no failure is filtered out: the API accepts exactly what
the schema declares. A new endpoint is covered as soon as it is in the schema;
a new detail route also adds its factory to OWNED_IDS.
A failure prints the curl command that reproduces it. See
docs/api.md#contract-conformance and ADR 0018.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import schemathesis
from django.core.wsgi import get_wsgi_application
from hypothesis import HealthCheck
from hypothesis import settings as hypothesis_settings

from apps.users.models import User
from core.tests.accounts import session_token

if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest_django.fixtures import Settings
    from schemathesis import Case
    from schemathesis.schemas import BaseSchema

CALLER = "conformance@example.com"
# One factory per resource with a detail route: given the caller, it returns
# the public id of an object the caller can see, creating it if needed. Half of
# the requests to a detail route draw from these ids (a 2xx when the id fits
# the route), the rest are unknown ids (a 404). Add a project's resource here,
# or every request to its detail route is a 404 and its 2xx goes unchecked.
OWNED_IDS: list[Callable[[User], str]] = [
    lambda caller: str(caller.public_id),
]
# Generated requests per operation, on top of the edge cases schemathesis always
# sends. They are the same on every run until Hypothesis or schemathesis changes.
EXAMPLES_PER_OPERATION = 50


@pytest.fixture
def caller_token(transactional_db: None) -> str:
    # transactional_db: the WSGI handler sends request_started, which closes the
    # connection of a test that runs inside a transaction
    # (apps/users/tests/ninja/test_url_script_prefix.py does the same).
    return session_token(CALLER)


@pytest.fixture
def api_schema(caller_token: str, settings: Settings) -> BaseSchema:
    staff_token = session_token("conformance-staff@example.com", is_staff=True)
    caller = User.objects.get(email=CALLER)
    owned_ids = [factory(caller) for factory in OWNED_IDS]
    # schemathesis sends its requests with the Host header "localhost".
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, "localhost"]
    config = schemathesis.Config.from_dict(
        {
            "generation": {
                # The test sends a real session token; generated credentials would
                # only be noise. ignored_auth still probes without it.
                "with-security-parameters": False,
                # PostgreSQL text cannot store NUL, so the API refuses \x00 in a
                # string (null_characters_not_allowed). schemathesis documents
                # this switch for servers and drivers that reject NUL bytes; a
                # pattern in the schema instead would be an oasdiff ERR
                # (request-property-pattern-added). See ADR 0018.
                "allow-x00": False,
            },
            # Half of the positive requests to a detail route use an id the caller
            # can see (OWNED_IDS); the rest are unknown ids, which must be 404.
            "dictionaries": {"caller": {"values": owned_ids}},
            "parameters": {"path.id": {"dictionary": "caller", "probability": 0.5}},
        }
    )
    return schemathesis.openapi.from_wsgi(
        "/api/v1/schema/",
        get_wsgi_application(),
        config=config,
        headers={"X-Session-Token": staff_token, "Accept": "application/json"},
    )


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
