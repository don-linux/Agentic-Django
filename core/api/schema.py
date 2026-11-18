"""
OpenAPI generation that documents the error responses of every operation.

See docs/api.md#error-responses-in-openapi.
"""

from __future__ import annotations

from typing import Any

from drf_spectacular import openapi
from drf_spectacular.plumbing import force_instance
from drf_spectacular.utils import OpenApiParameter
from drf_spectacular.utils import OpenApiResponse
from rest_framework.fields import empty
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer

from core.api.serializers import ErrorSerializer
from core.api.serializers import ValidationErrorSerializer

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH"})

WWW_AUTHENTICATE = OpenApiParameter(
    name="WWW-Authenticate",
    type=str,
    location=OpenApiParameter.HEADER,
    response=["401"],
    description="Authentication scheme advertised by the view (`X-Session-Token`).",
)
RETRY_AFTER = OpenApiParameter(
    name="Retry-After",
    type=int,
    location=OpenApiParameter.HEADER,
    response=["429"],
    description="Seconds until the client may retry.",
)


def forbid_additional_properties_on_requests(
    result: dict[str, Any],
    generator: Any,
    request: Any,
    public: bool,  # noqa: FBT001
) -> dict[str, Any]:
    """Reject undeclared keys on request components, matching BaseModelSerializer."""
    del generator, request, public
    for name, schema in result.get("components", {}).get("schemas", {}).items():
        if name.endswith("Request") and schema.get("type") == "object":
            schema.setdefault("additionalProperties", False)
    return result


def _can_be_skipped(serializer: Any, name: str) -> bool:
    """
    Whether DRF may leave the field out of a response.

    DRF skips only a declared field with required=False, no default and no
    allow_null, when the instance lacks the attribute. Fields generated from a
    model always render, even when blank=True makes them optional in requests.
    """
    if name not in getattr(type(serializer), "_declared_fields", {}):
        return False
    field = serializer.fields[name]
    return not field.required and field.default is empty and not field.allow_null


class AutoSchema(openapi.AutoSchema):
    # Private drf-spectacular 0.30 hooks: no public one also runs for responses
    # declared with @extend_schema. See docs/api.md#error-responses-in-openapi.
    def _get_response_bodies(self, direction: Any = "response") -> dict[str, Any]:
        responses: dict[str, Any] = super()._get_response_bodies(direction)
        if direction != "response":
            return responses
        for status_code, serializer in self._default_error_responses().items():
            if status_code not in responses:
                responses[status_code] = self._get_response_for_code(
                    serializer,
                    status_code,
                    direction=direction,
                )
        return responses

    def _map_basic_serializer(self, serializer: Any, direction: Any) -> dict[str, Any]:
        # Lists every property DRF always renders as required in responses, so
        # oasdiff reports a removed response field (docs/api.md#breaking-changes).
        # Sorted and left out when empty, like drf-spectacular's own lists:
        # OpenAPI 3.0 rejects an empty one.
        schema: dict[str, Any] = super()._map_basic_serializer(serializer, direction)
        if direction != "response" or not schema.get("properties"):
            return schema
        instance = force_instance(serializer)
        required = set(schema.get("required", []))
        required.update(
            name for name in schema["properties"] if not _can_be_skipped(instance, name)
        )
        if required:
            schema["required"] = sorted(required)
        return schema

    def get_override_parameters(self) -> list[Any]:
        parameters = list(super().get_override_parameters() or [])
        parameters.extend((WWW_AUTHENTICATE, RETRY_AFTER))
        return parameters

    def _parse_or_validation_response(self) -> OpenApiResponse:
        validation = self.resolve_serializer(
            force_instance(ValidationErrorSerializer),
            "response",
        )
        error = self.resolve_serializer(force_instance(ErrorSerializer), "response")
        return OpenApiResponse(
            response={"oneOf": [validation.ref, error.ref]},
            description=(
                "Validation error (`fields` present) or parse error (no `fields`)."
            ),
        )

    def _default_error_responses(self) -> dict[str, Any]:
        defaults: dict[str, Any] = {}
        if self.method in WRITE_METHODS:
            defaults["400"] = self._parse_or_validation_response()
            if self._only_instances_of(self.view.get_parsers(), JSONParser):
                defaults["415"] = OpenApiResponse(
                    ErrorSerializer,
                    description="The request Content-Type is not accepted.",
                )
        if self._only_instances_of(self.view.get_renderers(), JSONRenderer):
            defaults["406"] = OpenApiResponse(
                ErrorSerializer,
                description="The Accept header cannot be satisfied.",
            )
        if not all(isinstance(p, AllowAny) for p in self.view.get_permissions()):
            defaults["401"] = OpenApiResponse(
                ErrorSerializer,
                description="Not authenticated.",
            )
            defaults["403"] = OpenApiResponse(
                ErrorSerializer,
                description=(
                    "Permission denied, or CSRF failed on a cookie-authenticated write."
                    " Hidden objects are 404, not 403."
                ),
            )
        if "{" in self.path or self._paginated_list_can_404():
            defaults["404"] = OpenApiResponse(
                ErrorSerializer,
                description="Not found, not visible, or an invalid page number.",
            )
        if self.view.get_throttles():
            defaults["429"] = OpenApiResponse(
                ErrorSerializer,
                description="Throttled.",
            )
        return defaults

    def _paginated_list_can_404(self) -> bool:
        paginator = self._get_paginator()
        return bool(
            paginator
            and self._is_list_view()
            and isinstance(paginator, PageNumberPagination),
        )

    @staticmethod
    def _only_instances_of(instances: list[Any], cls: type[Any]) -> bool:
        return bool(instances) and all(isinstance(item, cls) for item in instances)
