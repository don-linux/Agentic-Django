from __future__ import annotations

from typing import TYPE_CHECKING
from typing import cast

from rest_framework.decorators import action
from rest_framework.mixins import ListModelMixin
from rest_framework.mixins import RetrieveModelMixin
from rest_framework.mixins import UpdateModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from apps.users.models import User
from apps.users.selectors import user_list
from apps.users.services import user_update
from core.api.mixins import PublicIdLookupMixin

from .serializers import UserSerializer

if TYPE_CHECKING:
    from django.db.models import QuerySet
    from rest_framework.request import Request
    from rest_framework.serializers import BaseSerializer


class UserViewSet(
    PublicIdLookupMixin,
    RetrieveModelMixin,
    ListModelMixin,
    UpdateModelMixin,
    GenericViewSet,
):
    serializer_class = UserSerializer
    # PATCH is the partial update. PUT is not exposed: with an optional `name` it
    # would accept {} and change nothing, which is PATCH under another name.
    http_method_names = ["get", "patch", "head", "options"]
    # The router derives the basename from this queryset; get_queryset() (the
    # user_list selector) is the source of truth for what a request sees.
    queryset = User.objects.order_by("public_id")

    def get_queryset(self) -> QuerySet[User]:
        if not self.request.user.is_authenticated or self.request.user.pk is None:
            return self.queryset.none()
        return user_list(user=self.request.user)

    def perform_update(self, serializer: BaseSerializer[User]) -> None:
        user = serializer.instance
        if not isinstance(user, User):
            msg = "UserViewSet.perform_update expected a User instance"
            raise TypeError(msg)
        name = serializer.validated_data.get("name", user.name)
        serializer.instance = user_update(user=user, name=name)

    @action(detail=False, methods=["get", "patch"])
    def me(self, request: Request) -> Response:
        # IsAuthenticated already answered anonymous requests with a 401, and
        # authentication loaded the user: use it without querying again.
        user = cast("User", request.user)
        if request.method == "PATCH":
            # Same path as PATCH /users/{id}/: validation, then user_update.
            serializer = self.get_serializer(user, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            self.perform_update(serializer)
        else:
            serializer = self.get_serializer(user)
        return Response(serializer.data)
