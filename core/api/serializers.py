from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from typing import Any

from django.db import models
from rest_framework import serializers


class UTCDateTimeField(serializers.DateTimeField):
    """
    A DateTimeField that renders UTC with a `Z` suffix, whatever TIME_ZONE is.

    A naive datetime in a request is read as UTC. See docs/api.md#representation.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("default_timezone", dt.UTC)
        super().__init__(*args, **kwargs)


# Base class for every API model serializer: a body key that is not a field is a
# 400 with the code `unknown_field`, not a silently ignored typo. No class
# docstring on purpose: drf-spectacular would publish it on every subclass.
# See docs/api.md#representation.
class BaseModelSerializer[M: models.Model](serializers.ModelSerializer[M]):
    # Model datetimes render in UTC (UTCDateTimeField). Declare hand-written
    # datetime fields with UTCDateTimeField too.
    serializer_field_mapping = {
        **serializers.ModelSerializer.serializer_field_mapping,
        models.DateTimeField: UTCDateTimeField,
    }

    def to_internal_value(self, data: Any) -> Any:
        if isinstance(data, Mapping):
            unknown = sorted(set(data) - set(self.fields))
            if unknown:
                raise serializers.ValidationError(
                    {name: ["Unknown field."] for name in unknown},
                    code="unknown_field",
                )
        return super().to_internal_value(data)


class ErrorSerializer(serializers.Serializer[Any]):
    """Body of every error response except validation errors (docs/api.md)."""

    code = serializers.CharField(help_text="Stable snake_case code from docs/api.md.")
    detail = serializers.CharField(help_text="English message for humans.")
    extra = serializers.DictField(
        required=False,
        help_text="Structured context for some domain errors.",
    )


class FieldErrorSerializer(serializers.Serializer[Any]):
    """One validation error of one field."""

    code = serializers.CharField()
    detail = serializers.CharField()


class ValidationErrorSerializer(serializers.Serializer[Any]):
    """Body of a 400 validation error, with the errors of each field."""

    code = serializers.CharField(help_text='Always "validation_error".')
    detail = serializers.CharField()

    def get_fields(self) -> dict[str, serializers.Field[Any, Any, Any, Any]]:
        # Added here because a class attribute named "fields" would shadow
        # Serializer.fields.
        fields = super().get_fields()
        fields["fields"] = serializers.DictField(
            child=serializers.ListField(child=FieldErrorSerializer()),
            help_text=(
                "Errors per field. Nested serializers nest further, and errors that "
                "belong to no single field go under non_field_errors."
            ),
        )
        return fields
