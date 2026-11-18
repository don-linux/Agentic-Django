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

## Admin history left by older copies

A project started from a copy of this template without `user_scrub_admin_history` may still show emails in the admin history: there, anonymizing scrubbed only the user's own rows, and a hard delete scrubbed none. Those rows are about users, email addresses or authenticators that no longer exist, so their `object_id` matches nothing and the service cannot reach them. Rewrite them once, after a backup, in `docker compose -f docker/prod/compose.yaml exec django python manage.py shell`:

```python
from allauth.account.models import EmailAddress
from allauth.mfa.models import Authenticator
from django.contrib.admin.models import LogEntry
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import TextField
from django.db.models.functions import Cast

from apps.users.models import User

DRY_RUN = True  # prints the counts; set it to False to rewrite
LABEL = "deleted@anonymized.invalid"

with transaction.atomic():
    for model in (User, EmailAddress, Authenticator):
        content_type = ContentType.objects.get_for_model(model)
        live_ids = model.objects.annotate(id_text=Cast("pk", TextField()))
        orphans = (
            LogEntry.objects.filter(content_type=content_type)
            .exclude(object_id__in=live_ids.values("id_text"))
            .exclude(object_repr__endswith="@anonymized.invalid")
        )
        count = orphans.count() if DRY_RUN else orphans.update(object_repr=LABEL)
        print(model._meta.label, count)
```

Rows about existing objects and rows already scrubbed stay as they are, so running it twice changes nothing. History about an address a user removed is rewritten too: no row is left to open from it.
