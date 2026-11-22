"""Path converter for user public ids: UUID in any case, never an integer pk."""

from __future__ import annotations


class PublicIdConverter:
    # Django's UUIDConverter is lowercase-only; tests/http/ send an uppercase id.
    regex = (
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    )

    def to_python(self, value: str) -> str:
        return value

    def to_url(self, value: object) -> str:
        return str(value)
