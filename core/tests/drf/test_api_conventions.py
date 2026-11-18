"""
API conventions that a new viewset must follow (AGENTS.md, docs/api.md).

Every viewset looks rows up by public_id through PublicIdLookupMixin, no API
route captures an integer pk, and client-driven filtering or ordering names its
fields explicitly and never a sensitive one.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Any

import pytest
from django.test import override_settings
from django.urls import include
from django.urls import path
from rest_framework import serializers
from rest_framework.mixins import RetrieveModelMixin
from rest_framework.permissions import IsAuthenticated
from rest_framework.relations import HyperlinkedIdentityField
from rest_framework.relations import HyperlinkedRelatedField
from rest_framework.relations import PrimaryKeyRelatedField
from rest_framework.routers import SimpleRouter
from rest_framework.viewsets import GenericViewSet

from apps.users.models import User
from config import urls as project_urls
from config.api_router import router as project_router
from core.api.mixins import PublicIdLookupMixin
from core.tests.routes import api_integer_pk_route_problems

if TYPE_CHECKING:
    from collections.abc import Sequence

    from django.test import Client

pytestmark = pytest.mark.django_db

_PK_LOOKUP_NAMES = frozenset({"pk", "id"})
_SENSITIVE_FIELDS = frozenset(
    {
        "password",
        "token",
        "secret",
        "session_key",
        "user_permissions",
    }
)


def client_driven_filter_problems(viewset_class: type[Any]) -> list[str]:
    """Return problems for unsafe client-driven filter/order/search config."""
    problems: list[str] = []
    name = viewset_class.__name__
    backends = [
        backend.__name__ if not isinstance(backend, str) else backend.rsplit(".", 1)[-1]
        for backend in getattr(viewset_class, "filter_backends", None) or []
    ]
    ordering_fields = getattr(viewset_class, "ordering_fields", None)
    if ordering_fields == "__all__":
        problems.append(f"{name}.ordering_fields=__all__")
    if "OrderingFilter" in backends and ordering_fields in (None, "__all__"):
        problems.append(f"{name}.OrderingFilter without an explicit whitelist")

    filterset_fields = getattr(viewset_class, "filterset_fields", None)
    if filterset_fields == "__all__":
        problems.append(f"{name}.filterset_fields=__all__")
    if (
        "DjangoFilterBackend" in backends
        and filterset_fields is None
        and not getattr(viewset_class, "filterset_class", None)
    ):
        problems.append(
            f"{name}.DjangoFilterBackend without filterset_fields or filterset_class"
        )

    search_fields = getattr(viewset_class, "search_fields", None)
    named: list[str] = []
    for value in (ordering_fields, filterset_fields, search_fields):
        if isinstance(value, str):
            continue
        if value:
            named.extend(str(field).lstrip("-") for field in value)
    problems.extend(
        f"{name} exposes {field}"
        for field in named
        if field in _SENSITIVE_FIELDS or field.endswith("password")
    )
    return problems


def test_registered_list_views_have_safe_client_filters() -> None:
    problems: list[str] = []
    for _, viewset, _ in project_router.registry:
        problems += client_driven_filter_problems(viewset)
    assert problems == []


def test_client_filter_check_flags_all_and_sensitive_fields() -> None:
    class BadViewSet(GenericViewSet):
        ordering_fields = "__all__"
        filterset_fields = ["name", "password"]
        search_fields = ["token"]
        filter_backends = [
            type("OrderingFilter", (), {}),
            type("DjangoFilterBackend", (), {}),
        ]

    problems = client_driven_filter_problems(BadViewSet)
    assert f"{BadViewSet.__name__}.ordering_fields=__all__" in problems
    assert (
        f"{BadViewSet.__name__}.OrderingFilter without an explicit whitelist"
        in problems
    )
    assert f"{BadViewSet.__name__} exposes password" in problems
    assert f"{BadViewSet.__name__} exposes token" in problems


def public_id_lookup_problems(
    registry: Sequence[tuple[str, type[Any], str | None]],
) -> list[str]:
    """Return viewsets / hyperlinked fields that would expose an integer pk."""
    problems: list[str] = []
    for _, viewset_class, _ in registry:
        name = viewset_class.__name__
        lookup = getattr(viewset_class, "lookup_field", "pk")
        if lookup in _PK_LOOKUP_NAMES or lookup != "public_id":
            problems.append(f"{name}.lookup_field={lookup}")
        serializer_class = getattr(viewset_class, "serializer_class", None)
        if serializer_class is None:
            continue
        try:
            fields = serializer_class().fields
        except Exception:  # noqa: BLE001 — a broken serializer is itself a problem
            problems.append(f"{name}.serializer_class is not instantiable")
            continue
        identity = fields.get("id")
        if identity is not None and getattr(identity, "source", None) in {
            "id",
            "pk",
            None,
        }:
            problems.append(f"{name}.serializer id source={identity.source!r}")
        for field_name, field in fields.items():
            hyperlinked = isinstance(
                field, HyperlinkedIdentityField | HyperlinkedRelatedField
            )
            if hyperlinked and field.lookup_field != "public_id":
                problems.append(
                    f"{name}.{field_name}.lookup_field={field.lookup_field}",
                )
            if isinstance(field, PrimaryKeyRelatedField):
                problems.append(f"{name}.{field_name} is a PrimaryKeyRelatedField")
    return problems


def test_registered_viewsets_lookup_by_public_id() -> None:
    assert public_id_lookup_problems(project_router.registry) == []


def test_registered_viewsets_use_the_public_id_lookup_mixin() -> None:
    missing = [
        viewset.__name__
        for _, viewset, _ in project_router.registry
        if not issubclass(viewset, PublicIdLookupMixin)
    ]
    assert missing == []


def test_lookup_check_flags_a_viewset_that_uses_pk() -> None:
    class ToySerializer(serializers.HyperlinkedModelSerializer):
        class Meta:
            model = User
            fields = ["id", "url"]

    class ToyViewSet(RetrieveModelMixin, GenericViewSet):
        queryset = User.objects.all()
        serializer_class = ToySerializer
        permission_classes = [IsAuthenticated]

    problems = public_id_lookup_problems([("toys", ToyViewSet, "toy")])
    assert "ToyViewSet.lookup_field=pk" in problems
    # HyperlinkedModelSerializer defaults url lookup to pk.
    assert any("url.lookup_field=pk" in item for item in problems)
    assert any("id source=" in item for item in problems)


def test_a_pk_viewset_would_serve_the_integer_primary_key(
    user: User,
    user_client: Client,
) -> None:
    """Live proof that a missed lookup_field leaks /api/v1/toys/<pk>/."""

    class ToySerializer(serializers.ModelSerializer):
        class Meta:
            model = User
            fields = ["id", "name"]

    class ToyViewSet(RetrieveModelMixin, GenericViewSet):
        queryset = User.objects.all()
        serializer_class = ToySerializer
        permission_classes = [IsAuthenticated]
        lookup_field = "pk"

    toy_router = SimpleRouter()
    toy_router.register("toys", ToyViewSet)

    urlpatterns = [
        path("api/v1/", include(toy_router.urls)),
        *project_urls.urlpatterns,
    ]

    with override_settings(
        ROOT_URLCONF=type("Urlconf", (), {"urlpatterns": urlpatterns})
    ):
        response = user_client.get(f"/api/v1/toys/{user.pk}/")

    assert response.status_code == HTTPStatus.OK
    assert response.json()["id"] == user.pk
    assert public_id_lookup_problems([("toys", ToyViewSet, "toy")]) != []

    # The integer route is exactly what the checker must reject.
    assert api_integer_pk_route_problems([f"api/v1/toys/(?P<pk>{user.pk})/"]) == [
        f"api/v1/toys/(?P<pk>{user.pk})/",
    ]
