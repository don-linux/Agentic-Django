# User deletion

A user row is never removed through the ORM: `User.delete()` and `User.objects...delete()` raise `ProtectedError`, which the API answers as 409 `protected`. Deleting a user would cascade into `admin.LogEntry`, allauth's email addresses and MFA rows, and anything a project later points at users. Pick one of three levels instead:

| Level | Reversible | Keeps the row | Use it for |
| --- | --- | --- | --- |
| [Deactivate](deactivate.md) | Yes | Yes | "Delete my account", suspensions, leavers |
| [Anonymize](anonymize.md) | No | Yes | An erasure request (GDPR and similar): **the recommended erase** |
| [Hard delete](hard-delete.md) | No | No | Test accounts and mistakes, by an operator only |

Anonymize is the right answer to "erase my data": it removes what identifies the person and keeps every row that points at the user valid. Hard delete also removes the history that points at the row, which is rarely what a project wants.
