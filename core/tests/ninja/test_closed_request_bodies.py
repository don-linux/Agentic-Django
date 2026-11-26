"""Every request body the API publishes is closed (AGENTS.md: extra="forbid").

A body built on BaseSchema instead of InputSchema would ignore an unknown key
instead of answering unknown_field, and schemathesis would not notice: it
sends unknown keys only to closed schemas. This walks every request body of
the published schema, through $ref, allOf, anyOf and oneOf, and every nested
object with properties.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest  # noqa: TC002  # runtime: Ninja signature
from ninja import Router

from core.api.api import api
from core.api.schemas import BaseSchema
from core.api.schemas import InputSchema
from core.tests.ninja.support import make_api

_REF_PREFIX = "#/components/schemas/"


def _open_objects(
    schema: dict[str, Any], components: dict[str, Any], where: str
) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    pending: list[tuple[dict[str, Any], str]] = [(schema, where)]
    while pending:
        node, at = pending.pop()
        ref = node.get("$ref")
        if isinstance(ref, str):
            name = ref.removeprefix(_REF_PREFIX)
            if name not in seen:
                seen.add(name)
                pending.append((components[name], f"{at} -> {name}"))
            continue
        for key in ("allOf", "anyOf", "oneOf"):
            pending.extend((part, at) for part in node.get(key, []))
        if isinstance(node.get("items"), dict):
            pending.append((node["items"], f"{at}[]"))
        properties = node.get("properties")
        if isinstance(properties, dict):
            if node.get("additionalProperties") is not False:
                problems.append(at)
            pending.extend((prop, f"{at}.{name}") for name, prop in properties.items())
    return problems


def _request_body_problems(document: dict[str, Any]) -> list[str]:
    components = document.get("components", {}).get("schemas", {})
    problems: list[str] = []
    for path, item in document["paths"].items():
        for method, operation in item.items():
            body = operation.get("requestBody")
            if not body:
                continue
            for media_type, content in body["content"].items():
                where = f"{method.upper()} {path} {media_type}"
                problems += _open_objects(content["schema"], components, where)
    return problems


def test_every_published_request_body_is_closed() -> None:
    assert _request_body_problems(api.get_openapi_schema()) == []


class _Open(BaseSchema):
    name: str


class _Closed(InputSchema):
    name: str


def test_a_body_on_base_schema_is_reported() -> None:
    # The negative case: the walk finds the open body and only that one.
    probe = make_api(throttle=[])
    router = Router()

    @router.post("/open/", auth=None)
    def open_body(request: HttpRequest, body: _Open) -> dict[str, bool]:
        return {"ok": True}

    @router.post("/closed/", auth=None)
    def closed_body(request: HttpRequest, body: _Closed) -> dict[str, bool]:
        return {"ok": True}

    probe.add_router("/", router)
    # path_prefix: the probe API is not mounted, so Ninja cannot reverse its root.
    problems = _request_body_problems(probe.get_openapi_schema(path_prefix=""))
    assert problems == ["POST /open/ application/json -> _Open"]
