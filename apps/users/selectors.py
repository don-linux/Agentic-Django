from __future__ import annotations

from typing import TYPE_CHECKING

from apps.users.models import User
from core.cache import read_through

if TYPE_CHECKING:
    import uuid

    from django.db.models import QuerySet

# The cache namespace of users: every write to a user invalidates it
# (apps/users/services.py and apps/users/signals.py).
USER_NAMESPACE = "users.user"
# Seconds a cached user may stay stale after a write that skipped invalidation.
USER_TTL = 300
# Part of the key. A cached model instance is a pickle of its fields: raise this
# when the User model changes, so copies with the old shape are never read.
USER_CACHE_VERSION = 1


def user_get(*, public_id: uuid.UUID) -> User:
    """
    Return the user with this public id, from the cache when it is there.

    Not for request.user, which authentication already loaded. The template's worked
    example of a cached selector: see docs/ARCHITECTURE.md#caching.
    """
    return read_through(
        USER_NAMESPACE,
        USER_CACHE_VERSION,
        public_id,
        ttl=USER_TTL,
        loader=lambda: User.objects.defer("password").get(public_id=public_id),
    )


def user_list(*, user: User) -> QuerySet[User]:
    """
    Users the requester may list: only themselves.

    The query shape lives here, not in the view. See docs/ARCHITECTURE.md#layers.
    """
    return User.objects.filter(pk=user.pk).order_by("public_id")
