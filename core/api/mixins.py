"""Reusable viewset lookup for PublicIdModel (and User.public_id)."""

from __future__ import annotations

# Case-insensitive UUID: Django's default uuid converter and a lowercase-only
# regex never reach the view, so DEBUG=True would return Django's HTML 404.
PUBLIC_ID_LOOKUP_REGEX = (
    "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


class PublicIdLookupMixin:
    # Look up rows by public_id, published as the {id} path parameter so the
    # path, the body, and the docs agree. Hyperlinked identity fields must set
    # lookup_url_kwarg = "id" too. No class docstring: spectacular would publish
    # it as the description of every action on a viewset that has none of its own.
    lookup_field = "public_id"
    lookup_url_kwarg: str | None = "id"
    lookup_value_regex = PUBLIC_ID_LOOKUP_REGEX
