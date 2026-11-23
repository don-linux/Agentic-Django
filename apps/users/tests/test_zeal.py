"""django-zeal fails a test request that runs an N+1 query (config/settings/test.py)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.conf import settings
from django.http import HttpRequest
from django.http import HttpResponse
from zeal.errors import NPlusOneError
from zeal.middleware import zeal_middleware

from apps.users.models import User
from apps.users.tests.factories import UserFactory

if TYPE_CHECKING:
    from django.test import RequestFactory

pytestmark = pytest.mark.django_db


def _n_plus_one(request: HttpRequest) -> HttpResponse:
    for user in User.objects.all():
        list(user.groups.all())
    return HttpResponse()


def test_every_test_request_goes_through_zeal() -> None:
    assert "zeal.middleware.zeal_middleware" in settings.MIDDLEWARE
    assert settings.ZEAL_RAISE is True


def test_an_n_plus_one_in_a_request_fails(rf: RequestFactory) -> None:
    UserFactory.create_batch(settings.ZEAL_NPLUSONE_THRESHOLD + 1)

    with pytest.raises(NPlusOneError):
        zeal_middleware(_n_plus_one)(rf.get("/"))
