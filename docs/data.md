# Data recipes

Opt-in recipes for data patterns. Copy one into a project only when it needs it: the template ships none of this code, no models, migrations or dependencies.

## Recipe: append-only tables

Use it for rows that are never corrected in place: ledgers and balance movements, events, consent records, audit trails. A correction is a new row, often in a separate table (a `LedgerReversal` that points to the movement it cancels), and a selector computes the effective state.

Four layers, because each one alone leaves a gap: the model and its queryset stop the ORM, the admin mixin stops staff, and a database trigger stops raw SQL and every other client of the database.

### Error

```python
# core/exceptions.py
class AppendOnlyError(ConflictError):
    """An update or delete on an append-only table (409 append_only)."""

    def __init__(self, message: str = "This record is append-only.") -> None:
        super().__init__(message, code="append_only")
```

`core.api.exceptions.register_exception_handlers` maps every `ApplicationError`, so the API answers `409 {"code": "append_only", ...}` with no other change. Register `append_only` in the "Error codes" table of [api.md](api.md) when you adopt the recipe.

### Model and queryset

```python
# core/models.py
from typing import Any

from django.db import models
from django.db.models.functions import Now

from core.exceptions import AppendOnlyError


class AppendOnlyQuerySet[M: models.Model](models.QuerySet[M]):
    def update(self, **kwargs: Any) -> int:
        raise AppendOnlyError

    def delete(self) -> tuple[int, dict[str, int]]:
        raise AppendOnlyError

    def bulk_update(self, objs: Any, fields: Any, batch_size: int | None = None) -> int:
        raise AppendOnlyError

    def update_or_create(
        self, defaults: Any = None, create_defaults: Any = None, **kwargs: Any
    ) -> tuple[M, bool]:
        raise AppendOnlyError

    def bulk_create(
        self,
        objs: Any,
        batch_size: int | None = None,
        ignore_conflicts: bool = False,
        update_conflicts: bool = False,
        update_fields: Any = None,
        unique_fields: Any = None,
    ) -> list[M]:
        # update_conflicts turns the insert into an upsert, which changes rows.
        if update_conflicts:
            raise AppendOnlyError
        return super().bulk_create(
            objs,
            batch_size=batch_size,
            ignore_conflicts=ignore_conflicts,
            update_fields=update_fields,
            unique_fields=unique_fields,
        )


AppendOnlyManager = models.Manager.from_queryset(AppendOnlyQuerySet)


class AppendOnlyModel(PublicIdModel):
    """Rows are inserted once and never updated or deleted."""

    created_at = models.DateTimeField(db_default=Now(), editable=False)

    objects = AppendOnlyManager()

    class Meta(PublicIdModel.Meta):
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise AppendOnlyError
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise AppendOnlyError
```

There is no `updated_at`: nothing changes after the insert. `bulk_create()` and `create()` still insert, but `bulk_create(update_conflicts=True)` is an upsert and raises. The async variants (`aupdate()`, `adelete()`, `abulk_create()`) call the methods above.

### Admin

```python
# core/admin.py
from django.db import models
from django.http import HttpRequest


class AppendOnlyAdminMixin:
    """Staff can read append-only rows, never change or delete them."""

    def has_change_permission(
        self, request: HttpRequest, obj: models.Model | None = None
    ) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: models.Model | None = None
    ) -> bool:
        return False
```

Without change permission the change page is read-only, and without delete permission the admin also drops the "Delete selected" action.

### Database trigger

```python
# core/migration_helpers.py
from django.db import migrations


def append_only_trigger(table: str) -> migrations.RunSQL:
    """
    Reject UPDATE, DELETE and TRUNCATE on `table`, from any client.

    `table` is the model's db_table, a constant from your code, never user input.
    """
    function = f"{table}_append_only"
    return migrations.RunSQL(
        sql=[
            f"""
            CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION '% on % is not allowed: the table is append-only',
                    TG_OP, TG_TABLE_NAME
                    USING ERRCODE = 'restrict_violation';
            END;
            $$
            """,
            f"""
            CREATE TRIGGER {table}_no_update_delete
                BEFORE UPDATE OR DELETE ON {table}
                FOR EACH ROW EXECUTE FUNCTION {function}()
            """,
            f"""
            CREATE TRIGGER {table}_no_truncate
                BEFORE TRUNCATE ON {table}
                FOR EACH STATEMENT EXECUTE FUNCTION {function}()
            """,
        ],
        reverse_sql=[
            f"DROP TRIGGER IF EXISTS {table}_no_truncate ON {table}",
            f"DROP TRIGGER IF EXISTS {table}_no_update_delete ON {table}",
            f"DROP FUNCTION IF EXISTS {function}()",
        ],
    )
```

Each concrete model adds it to its initial migration, after `CreateModel`:

```python
operations = [
    migrations.CreateModel(name="LedgerEntry", fields=[...]),
    append_only_trigger("ledger_ledgerentry"),
]
```

The trigger raises SQLSTATE `restrict_violation`, which Django turns into an `IntegrityError`. Through the ORM, `AppendOnlyError` fires first, so the API never reaches it.

### Rules

- Foreign keys that point to an append-only model use `on_delete=models.PROTECT`: a cascade would try to delete rows the trigger protects. When you adopt the recipe, extend `core/tests/test_on_delete_policy.py` so it fails on any other `on_delete` toward an `AppendOnlyModel` subclass. The test does not check that today.
- Corrections are new rows; a selector computes the current state (for example the sum of movements and reversals).

### Test checklist

- `instance.save()` on an existing row, `instance.delete()`, `queryset.update()`, `queryset.delete()`, `bulk_update()` and `update_or_create()` raise `AppendOnlyError`.
- A raw `UPDATE` through `connection.cursor()` fails because of the trigger (`IntegrityError`), and so do `DELETE` and `TRUNCATE`.
- The admin denies change and delete, and has no "Delete selected" action.
- An API write that hits the model returns 409 with `code == "append_only"`.

## Recipe: admin audit log

Use it in any back office where staff change data that must be traceable: who changed what, from what to what, and why. Django's `admin.LogEntry` stores a free-text message without the reason or the old and new values, and it covers neither custom admin actions nor services. The log builds on the append-only recipe above, because its rows are never changed.

### Model

```python
# apps/audit/models.py
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from core.models import AppendOnlyModel


class AdminAuditLog(AppendOnlyModel):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    # snake_case codes the project defines: user_created, user_changed, ...
    action = models.CharField(max_length=64)
    target_content_type = models.ForeignKey(
        ContentType, on_delete=models.PROTECT, related_name="+"
    )
    target_object_id = models.CharField(max_length=64)
    # {"field": [old, new]}
    changes = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    reason = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["target_content_type", "target_object_id", "created_at"],
                name="audit_log_target_idx",
            ),
            models.Index(fields=["actor", "created_at"], name="audit_log_actor_idx"),
        ]
```

Its initial migration ends with `append_only_trigger("audit_adminauditlog")`. `target_object_id` is the primary key as text, which fits integer and UUID keys alike. The log is admin-only and never leaves the server, so the pk is not exposed by the API.

### Service

```python
# apps/audit/services.py
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db import transaction
from django.http import HttpRequest

from apps.audit.models import AdminAuditLog
from core.net import get_client_ip


@transaction.atomic
def audit_log_record(
    *,
    actor: Any,
    action: str,
    target: models.Model,
    changes: dict[str, list[Any]] | None = None,
    reason: str = "",
    request: HttpRequest | None = None,
) -> AdminAuditLog:
    """Write one audit row; inside the caller's transaction it is a savepoint."""
    entry = AdminAuditLog(
        actor=actor,
        action=action,
        target_content_type=ContentType.objects.get_for_model(target),
        target_object_id=str(target.pk),
        changes=changes or {},
        reason=reason,
        # The trusted-proxy rule shared with throttling and django-axes.
        ip_address=(get_client_ip(request) or None) if request is not None else None,
    )
    entry.full_clean()
    entry.save()
    return entry
```

### Admin

```python
# apps/audit/admin.py
from typing import Any

from django import forms
from django.contrib import admin
from django.db import models
from django.http import HttpRequest

from apps.audit.models import AdminAuditLog
from apps.audit.services import audit_log_record
from core.admin import AppendOnlyAdminMixin


def _jsonable(value: Any) -> Any:
    """Model instances and querysets become primary keys; the encoder does the rest."""
    if isinstance(value, models.Model):
        return str(value.pk)
    if isinstance(value, models.QuerySet):
        return sorted(str(pk) for pk in value.values_list("pk", flat=True))
    if isinstance(value, (list, tuple)):
        # A many-to-many field's initial value is a list of instances.
        return sorted(str(_jsonable(item)) for item in value)
    return value


class AuditedAdminMixin:
    """Record every create, change and delete made through this admin."""

    # Never stored, not even as old and new values. password1 and password2 are
    # the fields of UserAdmin's add form.
    audit_exclude_fields: tuple[str, ...] = ("password", "password1", "password2")

    def save_model(
        self, request: HttpRequest, obj: Any, form: forms.ModelForm, change: bool
    ) -> None:
        super().save_model(request, obj, form, change)
        changes = {
            name: [
                _jsonable(form.initial.get(name)),
                _jsonable(form.cleaned_data.get(name)),
            ]
            for name in form.changed_data
            if name not in self.audit_exclude_fields
        }
        verb = "changed" if change else "created"
        audit_log_record(
            actor=request.user,
            action=f"{type(obj).__name__.lower()}_{verb}",
            target=obj,
            changes=changes,
            request=request,
        )

    def delete_model(self, request: HttpRequest, obj: Any) -> None:
        # Recorded first: the target's id is still known.
        self._record_delete(request, obj)
        super().delete_model(request, obj)

    def delete_queryset(self, request: HttpRequest, queryset: models.QuerySet) -> None:
        # "Delete selected" bypasses delete_model.
        for obj in queryset:
            self._record_delete(request, obj)
        super().delete_queryset(request, queryset)

    def _record_delete(self, request: HttpRequest, obj: Any) -> None:
        audit_log_record(
            actor=request.user,
            action=f"{type(obj).__name__.lower()}_deleted",
            target=obj,
            changes={},
            request=request,
        )


@admin.register(AdminAuditLog)
class AdminAuditLogAdmin(AppendOnlyAdminMixin, admin.ModelAdmin):
    list_display = [
        "created_at",
        "actor",
        "action",
        "target_content_type",
        "target_object_id",
    ]
    list_filter = ["action"]
    list_select_related = ["actor", "target_content_type"]
    search_fields = ["target_object_id"]
    show_full_result_count = False
```

The admin runs `save_model` inside the change view's transaction, so the row and its audit entry commit or roll back together. The mixin goes first in the bases (`class OrderAdmin(AuditedAdminMixin, admin.ModelAdmin)`). Password changes go through `UserAdmin.user_change_password`, not `save_model`: record `user_password_changed` there if the project needs it.

Admin actions record one row per object. For the template's `UserAdmin`:

```python
# apps/users/admin.py
@admin.register(User)
class UserAdmin(AuditedAdminMixin, auth_admin.UserAdmin):
    ...

    @admin.action(description="Deactivate selected users")
    def deactivate_users(self, request: HttpRequest, queryset: QuerySet[User]) -> None:
        others = queryset.exclude(pk=request.user.pk) if request.user.pk else queryset
        for user in others:
            user_deactivate(user=user)
            audit_log_record(
                actor=request.user,
                action="user_deactivated",
                target=user,
                request=request,
            )
        # reactivate_users does the same with user_reactivate and "user_reactivated".
```

### Test checklist

- Creating or changing an object in the admin writes one row with the right `action` and diff, and no password field.
- An admin action writes one row per object.
- Audit rows cannot be updated or deleted: ORM, admin and raw SQL (the append-only checklist).

When the project adopts it, add this rule to its AGENTS.md: "Every admin write and privileged service writes an `AdminAuditLog` row in the same transaction."

## User name

`User.name` is a free-text `CharField`: it accepts line breaks and HTML, stored as sent (the API neither trims nor converts it). Nothing renders it as HTML (the API is JSON only and emails are plain text), but escape it wherever a project shows it in markup or puts it in a header.
