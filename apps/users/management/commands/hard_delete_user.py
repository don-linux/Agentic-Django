"""
Permanently delete one user and every row that cascades from it.

The only code path that removes a user row: User.delete() and the user
queryset's delete() refuse (docs/user-deletion/hard-delete.md). Prefer
anonymizing; this is for test accounts and mistakes, run by an operator.
"""

from __future__ import annotations

import uuid
from collections import Counter
from typing import Any

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db import router
from django.db import transaction
from django.db.models import ProtectedError
from django.db.models.deletion import Collector

from apps.users.models import User
from apps.users.services import anonymized_email
from apps.users.services import user_deactivate
from apps.users.services import user_scrub_admin_history


class Command(BaseCommand):
    help = (
        "Permanently delete one user (by public id) and everything that cascades "
        "from it, after confirming the user's email."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("public_id", type=uuid.UUID)
        parser.add_argument(
            "--confirm",
            metavar="EMAIL",
            default="",
            help="The user's email, required with --noinput.",
        )
        parser.add_argument(
            "--noinput",
            "--no-input",
            action="store_false",
            dest="interactive",
            help="Do not prompt; --confirm must match the user's email.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            user = User.objects.get(public_id=options["public_id"])
        except User.DoesNotExist:
            msg = "No user has that public id."
            raise CommandError(msg) from None

        # Collector is not in Django's documentation. Django's admin builds its
        # delete confirmation on it (django.contrib.admin.utils.NestedObjects),
        # because it lists the cascade without deleting or sending signals.
        # _cascade_counts reads its data and fast_deletes, and
        # test_hard_delete_user pins both (docs/user-deletion/hard-delete.md).
        collector = Collector(using=router.db_for_write(User))
        try:
            # django-stubs 6.1.2 types collect() as
            # _IndexableCollection[Model | None], a Protocol whose __getitem__
            # also takes `index` by name; typeshed's list and tuple take it
            # positional-only, so Pyright 1.1.414 does not treat list[User] or
            # tuple[User] as that protocol.
            collector.collect([user])  # pyright: ignore[reportArgumentType]
        except ProtectedError as exc:
            msg = f"Rows that protect this user must go first: {exc.args[0]}"
            raise CommandError(msg) from exc
        self.stdout.write(f"Deleting {user.email} also deletes:")
        for label, count in sorted(_cascade_counts(collector).items()):
            self.stdout.write(f"  {label}: {count}")

        if options["interactive"]:
            typed = input("Type the user's email to delete them for good: ")
        else:
            typed = options["confirm"]
        if typed.strip() != user.email:
            msg = "The confirmation does not match the user's email; nothing deleted."
            raise CommandError(msg)

        with transaction.atomic(using=collector.using):
            # Signs the user out everywhere and drops cached copies first.
            user_deactivate(user=user)
            # Rows other admins wrote about the user survive the delete.
            user_scrub_admin_history(user=user, label=anonymized_email(user=user))
            collector.delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted user {user.public_id}."))


def _cascade_counts(collector: Collector) -> Counter[str]:
    counts: Counter[str] = Counter()
    for model, instances in collector.data.items():
        counts[model._meta.label] += len(instances)  # noqa: SLF001
    for queryset in collector.fast_deletes:
        counts[queryset.model._meta.label] += queryset.count()  # noqa: SLF001
    return counts
