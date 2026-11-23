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
# Part of the key. A cached model instance is a pickle of its field values: raise
# this when the User model's fields change, so copies with the old shape are never
# read. A change of base class alone keeps the pickle (docs/decisions/0014).
USER_CACHE_VERSION = 1


def user_get(*, public_id: uuid.UUID) -> User:
    """
    Return the user with this public id, from the cache when it is there.

    Not for request.user, which authentication already loaded. No operation of the
    template reads a user by id from the cache: this is the worked example of a
    cached selector for a project's hot reads, and its tests show the rules
    (docs/ARCHITECTURE.md#caching).
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


def user_visible(*, user: User, public_id: uuid.UUID) -> User:
    """
    The user with this public id, if the requester may see it.

    Raises User.DoesNotExist otherwise, which the API answers with 404: another
    user's object reads as no object. See docs/api.md#representation.
    """
    return user_list(user=user).get(public_id=public_id)
