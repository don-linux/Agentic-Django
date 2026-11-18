# 0004. Users are deactivated or anonymized, not deleted

- Status: Accepted
- Date: 2026-11-18

## Context

Deleting a user row cascades into the admin history, allauth and MFA rows, and whatever a project later points at users. That loses the record of what the account did.

## Decision

`User.delete()` and the user queryset's `delete()` raise `ProtectedError`. A user is deactivated (reversible) or anonymized (an erasure request); only the operator command `manage.py hard_delete_user` removes the row. Foreign keys default to `PROTECT`. See [user-deletion/](../user-deletion/README.md).

## Consequences

- The API answers a blocked delete with 409 `protected`.
- Services `user_deactivate`, `user_reactivate` and `user_anonymize` also sign the user out everywhere and drop cached copies.

## Alternatives

- A generic `is_deleted` soft-delete flag on every model: rejected, every query must remember to filter it, and unique constraints get harder.
- Hard delete with `CASCADE`: rejected, see the context.
