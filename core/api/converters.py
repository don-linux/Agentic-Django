"""Path converter for public ids: a UUID in either case, never an integer pk."""

from __future__ import annotations


class PublicIdConverter:
    # Django's UUIDConverter is lowercase-only; a client may send the uppercase
    # form, which is the same UUID (RFC 9562, section 4).
    regex = (
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    )

    def to_python(self, value: str) -> str:
        return value

    def to_url(self, value: object) -> str:
        return str(value)
