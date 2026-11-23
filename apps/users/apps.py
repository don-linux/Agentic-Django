from typing import override

from django.apps import AppConfig


class UsersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.users"
    verbose_name = "Users"

    @override
    def ready(self) -> None:
        # Connect the receivers that invalidate cached users (signals.py).
        from apps.users import signals  # noqa: F401, PLC0415
