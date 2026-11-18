from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from allauth.account.models import EmailAddress
from allauth.mfa.models import Authenticator
from django.conf import settings
from django.contrib.admin.models import LogEntry
from django.contrib.contenttypes.models import ContentType
from django.contrib.sessions.models import Session
from django.db import transaction
from django.db.models import Q
from django.db.models.functions import Now

from apps.users.selectors import USER_NAMESPACE
from core.cache import invalidate

if TYPE_CHECKING:
    from apps.users.models import User


@transaction.atomic
def user_update(*, user: User, name: str) -> User:
    user.name = name
    user.full_clean()
    user.save(update_fields=["name"])
    # Cached reads of users are dropped once this transaction commits.
    invalidate(USER_NAMESPACE)
    return user


@transaction.atomic
def user_deactivate(*, user: User) -> User:
    """
    Deactivate a user instead of deleting it, and sign it out everywhere.

    Deleting a User cascades to admin.LogEntry and allauth rows, losing the
    history of what that account did. An inactive user cannot log in.
    """
    user.is_active = False
    user.full_clean()
    user.save(update_fields=["is_active"])
    _delete_sessions(user=user)
    invalidate(USER_NAMESPACE)
    return user


@transaction.atomic
def user_reactivate(*, user: User) -> User:
    user.is_active = True
    user.full_clean()
    user.save(update_fields=["is_active"])
    # A login that raced with the deactivation may have left a session behind;
    # reactivating must not bring it back.
    _delete_sessions(user=user)
    invalidate(USER_NAMESPACE)
    return user


@transaction.atomic
def user_primary_email_sync(*, user: User, verify: bool = False) -> None:
    """
    Make `user.email` the user's primary allauth address.

    For writes that set User.email outside allauth (the admin, seed_dev). Another
    primary address is deleted; an existing address with this email is promoted
    as it is, and a new one is created verified, since staff typed it. With
    verify=True an existing address is verified too (seed_dev vouches for its
    users). Returns nothing: allauth ships no types, so its models would bring
    Any into this layer.
    """
    EmailAddress.objects.filter(user=user, primary=True).exclude(
        email=user.email,
    ).delete()
    email_address, _ = EmailAddress.objects.get_or_create(
        user=user,
        email=user.email,
        defaults={"primary": True, "verified": True},
    )
    update_fields: list[str] = []
    if not email_address.primary:
        email_address.primary = True
        update_fields.append("primary")
    if verify and not email_address.verified:
        email_address.verified = True
        update_fields.append("verified")
    if update_fields:
        email_address.save(update_fields=update_fields)


ANONYMIZED_EMAIL_DOMAIN = "anonymized.invalid"


@transaction.atomic
def user_anonymize(*, user: User) -> User:
    """
    Erase the user's personal data and keep the row. Irreversible.

    The answer to an erasure request (docs/user-deletion/anonymize.md): rows that
    point at the user stay valid, but nothing left identifies the person. It
    deactivates the user first, which signs them out everywhere.
    """
    user_deactivate(user=user)
    anonymous = anonymized_email(user=user)
    # Before the delete below: it needs the email address rows to find them.
    user_scrub_admin_history(user=user, label=anonymous)
    EmailAddress.objects.filter(user=user).delete()
    Authenticator.objects.filter(user=user).delete()
    user.email = anonymous
    user.name = ""
    user.last_login = None
    user.set_unusable_password()
    user.full_clean()
    user.save(update_fields=["email", "name", "last_login", "password"])
    invalidate(USER_NAMESPACE)
    return user


def anonymized_email(*, user: User) -> str:
    return f"deleted-{user.public_id}@{ANONYMIZED_EMAIL_DOMAIN}"


def user_scrub_admin_history(*, user: User, label: str) -> None:
    """
    Replace the user's email in the admin history about the user and its rows.

    LogEntry.object_repr keeps str(obj), which is the email for the user and its
    allauth email addresses, and only rows the user wrote cascade with it.
    See docs/user-deletion/anonymize.md.
    """
    rows = {
        type(user): [user.pk],
        EmailAddress: list(
            EmailAddress.objects.filter(user=user).values_list("pk", flat=True)
        ),
        Authenticator: list(
            Authenticator.objects.filter(user=user).values_list("pk", flat=True)
        ),
    }
    about_user = Q()
    for model, pks in rows.items():
        about_user |= Q(
            content_type=ContentType.objects.get_for_model(model),
            object_id__in=[str(pk) for pk in pks],
        )
    LogEntry.objects.filter(about_user).update(object_repr=label)


def _delete_sessions(*, user: User) -> None:
    """
    Delete the user's unexpired sessions (X-Session-Token too), in the DB and cache.

    Each one is decoded and deleted through the session engine: a queryset delete
    would leave the cached_db copy, and the token would keep working. Inside the
    caller's transaction, so a Valkey outage rolls the sign-out back.
    See docs/security.md#valkey-outages.
    """
    user_id = str(user.pk)
    session_keys = [
        session.session_key
        for session in Session.objects.filter(expire_date__gt=Now()).iterator()
        if session.get_decoded().get("_auth_user_id") == user_id
    ]
    session_store = import_module(settings.SESSION_ENGINE).SessionStore
    for session_key in session_keys:
        session_store(session_key=session_key).delete()
