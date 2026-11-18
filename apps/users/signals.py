"""
Cache invalidation for writes that do not go through the services.

The services invalidate the cached users themselves (apps/users/services.py).
The admin and allauth save users on their own, so these receivers are the
safety net. `queryset.update()`, `bulk_create()`, `bulk_update()` and `RunPython`
send no signals: call `core.cache.invalidate()` explicitly after them.
"""

from __future__ import annotations

from django.db.models.signals import post_delete
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.users.models import User
from apps.users.selectors import USER_NAMESPACE
from core.cache import invalidate

# Django saves this field alone on every login (update_last_login). Invalidating
# then would empty the namespace at each login and leave the cache useless.
_LOGIN_BOOKKEEPING = frozenset({"last_login"})


@receiver(post_save, sender=User)
def invalidate_users_on_save(
    sender: type[User],
    update_fields: frozenset[str] | None = None,
    **kwargs: object,
) -> None:
    if update_fields is not None and frozenset(update_fields) == _LOGIN_BOOKKEEPING:
        return
    invalidate(USER_NAMESPACE)


@receiver(post_delete, sender=User)
def invalidate_users_on_delete(sender: type[User], **kwargs: object) -> None:
    invalidate(USER_NAMESPACE)
