"""The test settings keep the DRF throttle scopes of base."""

from __future__ import annotations

import json

from django.conf import settings as django_settings

from config.settings import base as base_settings
from core.tests.production_settings import run_python

OUT_OF_REACH = "10000/min"
# base.py registers no scoped rate yet, so this stands in for a project that adds
# one, with an environment that also sets a low global limit.
RATES_WITH_A_SCOPE_IN_BASE = (
    "import json\n"
    "from config.settings import base\n"
    "base.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']['reports.generate'] = '5/min'\n"
    "from config.settings import test\n"
    "print(json.dumps(test.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']))\n"
)


def test_a_scoped_rate_of_base_exists_in_the_test_settings() -> None:
    # Without it, the endpoint that uses the scope raises ImproperlyConfigured in
    # every test, although it works in development and production.
    result = run_python(
        RATES_WITH_A_SCOPE_IN_BASE,
        env={"DJANGO_THROTTLE_ANON_RATE": "1/min"},
    )

    rates = json.loads(result.stdout.splitlines()[-1])
    assert rates["reports.generate"] == "5/min"
    # The global limits win over base.py and over the environment: a test run
    # must never be throttled by a rate meant for clients.
    assert rates["anon"] == OUT_OF_REACH
    assert rates["user"] == OUT_OF_REACH


def test_the_test_settings_add_no_scope_of_their_own() -> None:
    rates = django_settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    base_rates = base_settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]

    assert isinstance(rates, dict)
    assert isinstance(base_rates, dict)
    # Exactly the scopes of base.py: one that is not registered there still
    # raises ImproperlyConfigured under pytest, as it does in production.
    assert set(rates) == set(base_rates)
    assert rates["anon"] == rates["user"] == OUT_OF_REACH
    assert "reports.generate" not in rates
