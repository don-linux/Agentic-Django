# Anonymize a user

Irreversible: the personal data is erased and the row stays, so everything that points at the user keeps working. This is the recommended answer to an erasure request.

Code: `apps.users.services.user_anonymize(user=...)`, in one transaction. It:

1. Deactivates the user ([deactivate.md](deactivate.md)), which signs them out everywhere.
2. Replaces the email in the Django admin history (`admin.LogEntry.object_repr`) of the user, its allauth email addresses and its MFA authenticators, with `user_scrub_admin_history`.
3. Deletes the user's allauth email addresses and MFA authenticators.
4. Replaces the email with `deleted-<public_id>@anonymized.invalid`, which frees the real address for a new signup.
5. Clears `name` and `last_login`, and sets an unusable password.
6. Invalidates the cached user.

The `public_id` and the integer pk stay, so foreign keys and API references remain valid. A project that stores more personal data about users (addresses, uploads, free text, rows in other apps) extends this service to erase it too, in the same transaction, and adds a test for each. A model whose admin history shows personal data joins the rows `user_scrub_admin_history` rewrites.

It does not reach outside the database: application logs, error reports, backups and copies already sent to third parties follow their own retention.
