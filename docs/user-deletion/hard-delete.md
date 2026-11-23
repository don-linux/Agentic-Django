# Hard delete a user

Irreversible: the user row and every row that cascades from it are deleted. Use it only for test accounts and mistakes; for an erasure request, [anonymize](anonymize.md) instead.

It is a management command for an operator with shell access, the only code path that removes a user:

```bash
python manage.py hard_delete_user <public_id>
```

The command lists what the delete would remove (Django's `Collector`), then asks you to type the user's email. `Collector` is not in Django's documentation; Django's admin builds its delete confirmation on it, because it lists the cascade without deleting anything or sending signals. Running `Model.delete()` in a rolled-back transaction would list the same through documented API, but it executes the deletes and sends `post_delete`, so the template keeps `Collector`. The command reads its `data` and `fast_deletes`, and `apps/users/tests/test_hard_delete_user.py` pins both, so a Django upgrade that changes them fails there. In a script, pass `--noinput --confirm <email>`. In one transaction it signs the user out everywhere, scrubs the email from the admin history and deletes the rows, so a failure leaves the user as they were.

What a hard delete loses, beyond the user:

- The Django admin history the user wrote (`admin.LogEntry.user` cascades). The history other admins wrote about the user, its email addresses and authenticators stays, with the email replaced by `deleted-<public_id>@anonymized.invalid`. Rows left by an older copy of the template are cleaned once by hand ([anonymize.md](anonymize.md#admin-history-left-by-older-copies)).
- The user's allauth email addresses and MFA authenticators.
- Anything a project later points at users with `CASCADE`.

A foreign key with `PROTECT` stops the command with an error that names it: delete or reassign those rows first, or anonymize the user instead.
