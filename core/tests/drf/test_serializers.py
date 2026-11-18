from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from apps.users.models import User
from core.api.serializers import BaseModelSerializer
from core.api.serializers import UTCDateTimeField

if TYPE_CHECKING:
    from pytest_django.fixtures import Settings

SIX_PM_UTC = dt.datetime(2026, 11, 13, 18, 0, tzinfo=dt.UTC)


class _NameSerializer(BaseModelSerializer[User]):
    class Meta:
        model = User
        fields = ["name", "email"]
        read_only_fields = ["email"]


def test_unknown_keys_are_validation_errors_sorted_by_name() -> None:
    serializer = _NameSerializer(data={"name": "Ada", "zeta": 1, "alpha": 2})

    assert not serializer.is_valid()
    assert list(serializer.errors) == ["alpha", "zeta"]
    codes = {error.code for errors in serializer.errors.values() for error in errors}
    assert codes == {"unknown_field"}


def test_read_only_fields_are_known_and_ignored() -> None:
    serializer = _NameSerializer(data={"name": "Ada", "email": "x@example.com"})

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == {"name": "Ada"}


def test_a_body_that_is_not_an_object_keeps_drf_error() -> None:
    serializer = _NameSerializer(data=["not", "an", "object"])

    assert not serializer.is_valid()
    assert serializer.errors["non_field_errors"][0].code == "invalid"


class _DatesSerializer(BaseModelSerializer[User]):
    class Meta:
        model = User
        fields = ["date_joined"]


def test_utc_datetime_field_ignores_time_zone(settings: Settings) -> None:
    settings.TIME_ZONE = "America/Mexico_City"

    assert UTCDateTimeField().to_representation(SIX_PM_UTC) == "2026-11-13T18:00:00Z"


def test_naive_input_is_read_as_utc(settings: Settings) -> None:
    settings.TIME_ZONE = "America/Mexico_City"

    assert UTCDateTimeField().to_internal_value("2026-11-13T18:00:00") == SIX_PM_UTC


def test_model_datetimes_render_in_utc(settings: Settings) -> None:
    settings.TIME_ZONE = "America/Mexico_City"
    user = User(date_joined=SIX_PM_UTC)

    assert _DatesSerializer(user).data["date_joined"] == "2026-11-13T18:00:00Z"
    # Plain DRF follows TIME_ZONE, which is the behaviour BaseModelSerializer prevents.
