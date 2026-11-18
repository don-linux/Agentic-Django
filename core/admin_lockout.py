"""django-axes hooks that keep its lockout on the Django admin login only."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.urls import NoReverseMatch
from django.urls import reverse

if TYPE_CHECKING:
    from django.http import HttpRequest


def is_outside_admin(
    request: HttpRequest, credentials: dict[str, object] | None
) -> bool:
    """
    AXES_WHITELIST_CALLABLE: axes ignores every login attempt outside the admin.

    Without it, AXES_ONLY_ADMIN_SITE still counts those failures and can lock the
    admin. See docs/security.md#admin-lockout.
    """
    try:
        admin_prefix = reverse("admin:index")
    except NoReverseMatch:
        return True
    return not getattr(request, "path", "").startswith(admin_prefix)
