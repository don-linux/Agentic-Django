"""REST conventions held by the DRF router and view classes."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.test import override_settings
from django.urls import path
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.routers import SimpleRouter
from rest_framework.views import APIView

from config import api_router
from config import urls as project_urls

if TYPE_CHECKING:
    from django.test import Client
    from rest_framework.request import Request

pytestmark = pytest.mark.django_db


class _PublicCatalogView(APIView):
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        return Response({"items": []}, headers={"Cache-Control": "public, max-age=60"})


# Test-only URLconf: the project routes plus a view that opts into caching.
urlpatterns = [
    path("api/v1/test-public-catalog/", _PublicCatalogView.as_view()),
    *project_urls.urlpatterns,
]
handler404 = project_urls.handler404


def test_router_is_the_same_in_every_environment() -> None:
    # DefaultRouter (a SimpleRouter subclass) would add an API root view and
    # format-suffix routes; the template never builds it, whatever DEBUG is.
    assert type(api_router.router) is SimpleRouter


@override_settings(ROOT_URLCONF=__name__)
def test_a_view_can_opt_into_caching(client: Client) -> None:
    response = client.get("/api/v1/test-public-catalog/")

    assert response.headers["Cache-Control"] == "public, max-age=60"
