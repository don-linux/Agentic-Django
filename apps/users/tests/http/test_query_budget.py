"""
Exact query budgets per endpoint (AGENTS.md, "Conventions").

Measured with the test cache (LocMem) and cached_db sessions. Production has the
same shape: Valkey serves the session, the throttles and the rate limits, so none
of them adds a query.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from allauth.account.models import EmailAddress
from django.test import Client

from apps.users.models import User
from apps.users.tests.factories import UserFactory
from core.tests.credentials import PASSWORD

if TYPE_CHECKING:
    from conftest import ConstantQueriesCheck

pytestmark = pytest.mark.django_db

LOGIN_URL = "/_allauth/app/v1/auth/login"


@pytest.fixture
def budget_user() -> User:
    user = UserFactory(email="budget@example.com", password=PASSWORD)
    EmailAddress.objects.create(
        user=user, email=user.email, primary=True, verified=True
    )
    return user


@pytest.fixture
def session_client(budget_user: User) -> Client:
    login = Client().post(
        LOGIN_URL,
        {"email": budget_user.email, "password": PASSWORD},
        content_type="application/json",
    )
    return Client(headers={"x-session-token": login.json()["meta"]["session_token"]})


def test_me_budget(session_client: Client, django_assert_num_queries) -> None:
    # The user. The session comes from the cache (cached_db), and the view
    # serializes request.user instead of loading it again.
    with django_assert_num_queries(1):
        session_client.get("/api/v1/users/me/")


def test_list_budget(session_client: Client, django_assert_num_queries) -> None:
    # The user, the page count, the page.
    with django_assert_num_queries(3):
        session_client.get("/api/v1/users/")


def test_update_budget(
    session_client: Client,
    budget_user: User,
    django_assert_num_queries,
) -> None:
    # The user, the object, then the service: savepoint, full_clean() uniqueness
    # checks for email, public_id and the case-insensitive email constraint, the
    # UPDATE, and the savepoint release.
    with django_assert_num_queries(8):
        session_client.patch(
            f"/api/v1/users/{budget_user.public_id}/",
            {"name": "New name"},
            content_type="application/json",
        )


def test_update_me_budget(session_client: Client, django_assert_num_queries) -> None:
    # Same as the detail PATCH minus the object lookup: /me reuses request.user.
    with django_assert_num_queries(7):
        session_client.patch(
            "/api/v1/users/me/", {"name": "New name"}, content_type="application/json"
        )


def test_login_budget(
    budget_user: User, django_assert_max_num_queries, client: Client
) -> None:
    # An upper bound: allauth internals may change across patch releases (13 today).
    with django_assert_max_num_queries(14):
        client.post(
            LOGIN_URL,
            {"email": budget_user.email, "password": PASSWORD},
            content_type="application/json",
        )


def test_list_query_count_does_not_grow_with_other_users(
    session_client: Client,
    assert_constant_queries: ConstantQueriesCheck,
) -> None:
    assert_constant_queries(
        lambda: session_client.get("/api/v1/users/"),
        lambda: UserFactory.create_batch(5),
    )


def test_constant_queries_check_catches_an_n_plus_one(
    assert_constant_queries: ConstantQueriesCheck,
) -> None:
    def n_plus_one() -> None:
        for user in User.objects.all():
            list(EmailAddress.objects.filter(user=user))

    UserFactory()
    with pytest.raises(AssertionError, match="N\\+1"):
        assert_constant_queries(n_plus_one, lambda: UserFactory.create_batch(3))
