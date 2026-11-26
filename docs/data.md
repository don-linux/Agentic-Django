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
# core/models.py, next to BaseModel: add Collection and Mapping to its
# TYPE_CHECKING imports from collections.abc, and import the error.
from core.exceptions import AppendOnlyError


class AppendOnlyQuerySet[M: models.Model](models.QuerySet[M]):
    """Refuses every ORM write except an insert."""

    @override
    def update(self, **kwargs: object) -> int:
        raise AppendOnlyError

    @override
    def delete(self) -> tuple[int, dict[str, int]]:
        raise AppendOnlyError

    @override
    def bulk_update(
        self, objs: Iterable[M], fields: Iterable[str], batch_size: int | None = None
    ) -> int:
        raise AppendOnlyError

    @override
    def update_or_create(
        self,
        defaults: Mapping[str, object] | None = None,
        create_defaults: Mapping[str, object] | None = None,
        **kwargs: object,
    ) -> tuple[M, bool]:
        raise AppendOnlyError

    @override
    def bulk_create(
        self,
        objs: Iterable[M],
        batch_size: int | None = None,
        ignore_conflicts: bool = False,
        update_conflicts: bool = False,
        update_fields: Collection[str] | None = None,
        unique_fields: Collection[str] | None = None,
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

    created_at: models.DateTimeField[_DateTimeIn, datetime] = models.DateTimeField(
        db_default=Now(), editable=False
    )

    objects = AppendOnlyManager()

    class Meta(PublicIdModel.Meta):
        abstract = True

    @override
    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise AppendOnlyError
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )

    @override
    def delete(
        self, using: object = None, keep_parents: bool = False
    ) -> tuple[int, dict[str, int]]:
        raise AppendOnlyError
```

There is no `updated_at`: nothing changes after the insert. `bulk_create()` and `create()` still insert, but `bulk_create(update_conflicts=True)` is an upsert and raises. The async variants (`aupdate()`, `adelete()`, `abulk_create()`) call the methods above.

### Admin

```python
# core/admin.py
from typing import TYPE_CHECKING
from typing import override

from django.contrib import admin
from django.db import models

if TYPE_CHECKING:
    from django.http import HttpRequest


class AppendOnlyAdmin[M: models.Model](admin.ModelAdmin[M]):
    """Staff can read append-only rows, never change or delete them."""

    @override
    def has_change_permission(self, request: HttpRequest, obj: M | None = None) -> bool:
        return False

    @override
    def has_delete_permission(self, request: HttpRequest, obj: M | None = None) -> bool:
        return False
```

An append-only model's admin derives from it (`class LedgerEntryAdmin(AppendOnlyAdmin[LedgerEntry])`). Without change permission the change page is read-only, and without delete permission the admin also drops the "Delete selected" action.

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
from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from core.models import AppendOnlyModel

if TYPE_CHECKING:
    from django.db.models.expressions import Combinable

    from apps.users.models import User

    # {"field": [old, new]}, as the JSON encoder writes the values.
    type Changes = dict[str, list[object]]
    type _IpIn = str | int | Combinable | None


class AdminAuditLog(AppendOnlyModel):
    actor: models.ForeignKey[User | Combinable, User] = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    # snake_case codes the project defines: user_created, user_changed, ...
    action: models.CharField[str | int | Combinable, str] = models.CharField(
        max_length=64
    )
    target_content_type: models.ForeignKey[ContentType | Combinable, ContentType] = (
        models.ForeignKey(ContentType, on_delete=models.PROTECT, related_name="+")
    )
    target_object_id: models.CharField[str | int | Combinable, str] = models.CharField(
        max_length=64
    )
    changes: models.JSONField[Changes | Combinable, Changes] = models.JSONField(
        default=dict, blank=True, encoder=DjangoJSONEncoder
    )
    reason: models.TextField[str | Combinable, str] = models.TextField(blank=True)
    ip_address: models.GenericIPAddressField[_IpIn, str | None] = (
        models.GenericIPAddressField(null=True, blank=True)
    )

    class Meta(AppendOnlyModel.Meta):
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
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from apps.audit.models import AdminAuditLog

if TYPE_CHECKING:
    from django.db import models

    from apps.users.models import User


@dataclass(frozen=True)
class Actor:
    """Who made the change, and from which client IP when it is known."""

    user: User
    ip_address: str | None = None


@transaction.atomic
def audit_log_record(
    *,
    actor: Actor,
    action: str,
    target: models.Model,
    changes: dict[str, list[object]] | None = None,
    reason: str = "",
) -> AdminAuditLog:
    """Write one audit row; inside the caller's transaction it is a savepoint."""
    entry = AdminAuditLog(
        actor=actor.user,
        action=action,
        target_content_type=ContentType.objects.get_for_model(target),
        target_object_id=str(target.pk),
        changes=changes or {},
        reason=reason,
        ip_address=actor.ip_address,
    )
    entry.full_clean()
    entry.save()
    return entry
```

### Admin

```python
# apps/audit/admin.py
from typing import TYPE_CHECKING
from typing import override

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db import models

from apps.audit.models import AdminAuditLog
from apps.audit.services import Actor
from apps.audit.services import audit_log_record
from apps.users.models import User
from core.admin import AppendOnlyAdmin
from core.net import get_client_ip

if TYPE_CHECKING:
    from django import forms
    from django.db.models import QuerySet
    from django.http import HttpRequest


def _jsonable(value: object) -> object:
    """Model instances and querysets become primary keys; the encoder does the rest."""
    if isinstance(value, models.Model):
        return str(value.pk)
    if isinstance(value, models.QuerySet):
        return sorted(str(pk) for pk in value.values_list("pk", flat=True))
    if isinstance(value, list | tuple):
        # A many-to-many field's initial value is a list of instances.
        return sorted(str(_jsonable(item)) for item in value)
    return value


def audit_actor(request: HttpRequest) -> Actor:
    """The staff user of an admin request, and the client IP."""
    # The admin site lets only an active staff user in.
    if not isinstance(request.user, User):
        raise PermissionDenied
    # The trusted-proxy rule shared with throttling and django-axes.
    return Actor(user=request.user, ip_address=get_client_ip(request) or None)


class AuditedAdmin[M: models.Model](admin.ModelAdmin[M]):
    """Record every create, change and delete made through this admin."""

    # Never stored, not even as old and new values. password1 and password2 are
    # the fields of UserAdmin's add form.
    audit_exclude_fields: tuple[str, ...] = ("password", "password1", "password2")

    @override
    def save_model(
        self, request: HttpRequest, obj: M, form: forms.ModelForm[M], change: bool
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
            actor=audit_actor(request),
            action=f"{type(obj).__name__.lower()}_{verb}",
            target=obj,
            changes=changes,
        )

    @override
    def delete_model(self, request: HttpRequest, obj: M) -> None:
        # Recorded first: the target's id is still known.
        self._record_delete(request, obj)
        super().delete_model(request, obj)

    @override
    def delete_queryset(self, request: HttpRequest, queryset: QuerySet[M]) -> None:
        # "Delete selected" bypasses delete_model.
        for obj in queryset:
            self._record_delete(request, obj)
        super().delete_queryset(request, queryset)

    def _record_delete(self, request: HttpRequest, obj: M) -> None:
        audit_log_record(
            actor=audit_actor(request),
            action=f"{type(obj).__name__.lower()}_deleted",
            target=obj,
            changes={},
        )


@admin.register(AdminAuditLog)
class AdminAuditLogAdmin(AppendOnlyAdmin[AdminAuditLog]):
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

The admin runs `save_model` inside the change view's transaction, so the row and its audit entry commit or roll back together. An audited model's admin derives from `AuditedAdmin` (`class OrderAdmin(AuditedAdmin[Order])`), first when it has other bases. Password changes go through `UserAdmin.user_change_password`, not `save_model`: record `user_password_changed` there if the project needs it.

Admin actions record one row per object, and keep their own guards. For the template's `UserAdmin`:

```python
# apps/users/admin.py
from apps.audit.admin import AuditedAdmin
from apps.audit.admin import audit_actor
from apps.audit.services import audit_log_record


@admin.register(User)
class UserAdmin(AuditedAdmin[User], auth_admin.UserAdmin):
    ...

    # permissions: a staff user who may only view users must not run them.
    @admin.action(description="Deactivate selected users", permissions=["change"])
    def deactivate_users(self, request: HttpRequest, queryset: QuerySet[User]) -> None:
        others = queryset.exclude(pk=request.user.pk) if request.user.pk else queryset
        if not _is_superuser(request.user):
            others = others.exclude(is_staff=True).exclude(is_superuser=True)
        count = 0
        for user in others:
            user_deactivate(user=user)
            audit_log_record(
                actor=audit_actor(request), action="user_deactivated", target=user
            )
            count += 1
        ...  # the messages stay as they are
        # reactivate_users does the same with user_reactivate and "user_reactivated".
```

### Test checklist

- Creating or changing an object in the admin writes one row with the right `action` and diff, and no password field.
- An admin action writes one row per object.
- Audit rows cannot be updated or deleted: ORM, admin and raw SQL (the append-only checklist).

When the project adopts it, add this rule to its AGENTS.md: "Every admin write and privileged service writes an `AdminAuditLog` row in the same transaction."

## User name

`User.name` is a free-text `CharField`: it accepts line breaks and HTML, stored as sent (the API neither trims nor converts it). Nothing renders it as HTML (the API is JSON only and emails are plain text), but escape it wherever a project shows it in markup or puts it in a header.
