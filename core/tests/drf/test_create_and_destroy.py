"""
DRF's 201 and 204 contract for a new resource (docs/api.md, Methods and status codes).

A throwaway viewset proves what a project's first create and delete endpoints
return: 201 with a Location at the public id, and 204 with an empty body. It
also pins the trap that DRF only sets Location when the serializer has a `url`.
"""

from __future__ import annotations

import uuid
from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Any

import pytest
from django.test import override_settings
from django.urls import include
from django.urls import path
from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.mixins import CreateModelMixin
from rest_framework.mixins import DestroyModelMixin
from rest_framework.permissions import AllowAny
from rest_framework.routers import SimpleRouter
from rest_framework.viewsets import GenericViewSet

from config import urls as project_urls
from core.api.mixins import PublicIdLookupMixin

if TYPE_CHECKING:
    from django.test import Client

pytestmark = pytest.mark.django_db

_ITEMS: dict[str, Any] = {}


class _Item:
    def __init__(self, public_id: uuid.UUID, name: str) -> None:
        self.public_id = public_id
        self.name = name


class _ItemSerializer(serializers.Serializer):
    id = serializers.UUIDField(source="public_id", read_only=True)
    name = serializers.CharField()
    url = serializers.SerializerMethodField()

    def get_url(self, obj: _Item) -> str:
        return self.context["request"].build_absolute_uri(
            f"/api/v1/items/{obj.public_id}/",
        )

    def create(self, validated_data: dict[str, Any]) -> _Item:
        item = _Item(uuid.uuid7(), validated_data["name"])
        _ITEMS[str(item.public_id)] = item
        return item


class _ItemViewSet(
    PublicIdLookupMixin, CreateModelMixin, DestroyModelMixin, GenericViewSet
):
    permission_classes = [AllowAny]
    serializer_class = _ItemSerializer

    def get_object(self) -> _Item:
        try:
            return _ITEMS[str(self.kwargs["id"])]
        except KeyError:
            raise NotFound from None

    def perform_destroy(self, instance: _Item) -> None:
        _ITEMS.pop(str(instance.public_id), None)


def _item_urlpatterns() -> list[Any]:
    item_router = SimpleRouter()
    item_router.register("items", _ItemViewSet, basename="item")
    return [
        path("api/v1/", include(item_router.urls)),
        *project_urls.urlpatterns,
    ]


@override_settings(ROOT_URLCONF="core.tests.drf.test_create_and_destroy")
def test_create_returns_201_with_location_at_public_id(client: Client) -> None:
    _ITEMS.clear()
    # ROOT_URLCONF points at this module; urlpatterns is set below.
    response = client.post(
        "/api/v1/items/", {"name": "Notebook"}, content_type="application/json"
    )

    assert response.status_code == HTTPStatus.CREATED
    public_id = response.json()["id"]
    expected = f"http://testserver/api/v1/items/{public_id}/"
    assert response.headers["Location"] == expected
    assert response.json()["url"] == expected
    _ITEMS.clear()


@override_settings(ROOT_URLCONF="core.tests.drf.test_create_and_destroy")
def test_destroy_returns_204_with_an_empty_body(client: Client) -> None:
    _ITEMS.clear()
    item = _Item(uuid.uuid7(), "Tmp")
    _ITEMS[str(item.public_id)] = item

    response = client.delete(f"/api/v1/items/{item.public_id}/")

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert response.content == b""
    assert str(item.public_id) not in _ITEMS


def test_create_without_a_url_field_omits_location(client: Client) -> None:
    """DRF only sets Location when the serializer has a `url` field (the trap)."""

    class BareSerializer(serializers.Serializer):
        id = serializers.UUIDField(source="public_id", read_only=True)
        name = serializers.CharField()

        def create(self, validated_data: dict[str, Any]) -> _Item:
            return _Item(uuid.uuid7(), validated_data["name"])

    class BareViewSet(CreateModelMixin, GenericViewSet):
        permission_classes = [AllowAny]
        serializer_class = BareSerializer
        lookup_field = "public_id"

    bare_router = SimpleRouter()
    bare_router.register("bare-items", BareViewSet, basename="bare-item")
    urlpatterns = [
        path("api/v1/", include(bare_router.urls)),
        *project_urls.urlpatterns,
    ]
    with override_settings(
        ROOT_URLCONF=type("Urlconf", (), {"urlpatterns": urlpatterns})
    ):
        response = client.post(
            "/api/v1/bare-items/", {"name": "X"}, content_type="application/json"
        )

    assert response.status_code == HTTPStatus.CREATED
    assert "Location" not in response.headers


# Module-level urlpatterns for the 201/204 tests that use ROOT_URLCONF=__name__.
urlpatterns = _item_urlpatterns()
