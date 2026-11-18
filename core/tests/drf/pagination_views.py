"""DRF list views over User, shared by the pagination tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rest_framework import serializers
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny
from rest_framework.test import APIRequestFactory

from apps.users.models import User
from core.api.pagination import CachedCountPageNumberPagination
from core.api.pagination import LimitCursorPagination

if TYPE_CHECKING:
    from rest_framework.response import Response


class _EmailSerializer(serializers.ModelSerializer[User]):
    class Meta:
        model = User
        fields = ["email"]


class _UnorderedUsersView(ListAPIView[User]):
    permission_classes = [AllowAny]
    serializer_class = _EmailSerializer
    queryset = User.objects.all()


class _OrderedUsersView(_UnorderedUsersView):
    queryset = User.objects.order_by("pk")


class _CachedCountUsersView(_OrderedUsersView):
    pagination_class = CachedCountPageNumberPagination


class _CursorUsersView(ListAPIView[User]):
    permission_classes = [AllowAny]
    serializer_class = _EmailSerializer
    queryset = User.objects.all()
    pagination_class = LimitCursorPagination


def _list(view: type[ListAPIView[User]], query: str = "") -> Response:
    request = APIRequestFactory().get(f"/users/{query}")
    return view.as_view()(request)


def _create_users(count: int) -> None:
    User.objects.bulk_create(User(email=f"user{n}@example.com") for n in range(count))
