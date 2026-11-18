"""
Idempotent development data: a superuser, a regular user, and app hooks.

See docs/development.md#seed-dev.
"""

from __future__ import annotations

import importlib
import importlib.util
from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db import transaction

from apps.users.models import User
from apps.users.services import user_primary_email_sync
from apps.users.services import user_update

if TYPE_CHECKING:
    from argparse import ArgumentParser

DEFAULT_PASSWORD = "dev-password-123"  # noqa: S105 - development only
SEED_USERS = (
    ("admin@example.com", "Dev Admin", True),
    ("user@example.com", "Dev User", False),
)


class Command(BaseCommand):
    help = "Create or refresh development users and run each app's seed_dev() hook."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--password",
            default=DEFAULT_PASSWORD,
            help="Password for the seeded users.",
        )
        parser.add_argument(
            "--allow-non-debug",
            action="store_true",
            help="Run even when DEBUG is False. Never use it in production.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.DEBUG and not options["allow_non_debug"]:
            msg = "seed_dev only runs with DEBUG=True (or --allow-non-debug)."
            raise CommandError(msg)

        with transaction.atomic():
            for email, name, is_superuser in SEED_USERS:
                user = _seed_user(
                    email=email,
                    name=name,
                    is_superuser=is_superuser,
                    password=options["password"],
                )
                self.stdout.write(f"Seeded {user.email}")
            for app in settings.LOCAL_APPS:
                if _run_app_hook(app):
                    self.stdout.write(f"Ran {app}.seed.seed_dev()")

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Password for the seeded users: {options['password']}"
            ),
        )


def _seed_user(*, email: str, name: str, is_superuser: bool, password: str) -> User:
    email = User.objects.normalize_email(email)
    user = User.objects.filter(email=email).first()
    if user is None:
        user = User.objects.filter(email__iexact=email).first()
    if user is None:
        create = (
            User.objects.create_superuser if is_superuser else User.objects.create_user
        )
        user = create(email, password)
    else:
        user.email = email
        user.set_password(password)
        user.is_active = True
        user.is_staff = is_superuser
        user.is_superuser = is_superuser
        user.save(
            update_fields=["email", "password", "is_active", "is_staff", "is_superuser"]
        )
    user_update(user=user, name=name)
    user_primary_email_sync(user=user, verify=True)
    return user


def _run_app_hook(app: str) -> bool:
    module_name = f"{app}.seed"
    if importlib.util.find_spec(module_name) is None:
        return False
    importlib.import_module(module_name).seed_dev()
    return True
