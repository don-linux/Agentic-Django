from rest_framework import serializers

from apps.users.models import User
from core.api.serializers import BaseModelSerializer


class UserSerializer(BaseModelSerializer[User]):
    # The public UUID is the resource id; the integer pk never leaves the server.
    id = serializers.UUIDField(source="public_id", read_only=True)

    class Meta:
        model = User
        fields = ["id", "name", "url"]

        extra_kwargs = {
            "url": {
                "view_name": "api:user-detail",
                "lookup_field": "public_id",
                "lookup_url_kwarg": "id",
            },
        }
