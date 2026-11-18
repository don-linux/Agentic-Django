# Deactivate a user

Reversible: the account stops working and its data stays. This is what "delete my account" does.

- Code: `apps.users.services.user_deactivate(user=...)`; `user_reactivate` undoes it.
- It sets `is_active = False`, so the user cannot log in, deletes every session the user has (API tokens and admin sessions, from the database and from Valkey), and invalidates the cached user.
- The admin offers it as the "Deactivate selected users" action and as the `is_active` box, and both go through the service. Only a superuser can deactivate a staff account ([../security.md#admin-permissions](../security.md#admin-permissions)).
- Reactivating deletes any session left over too, so an old token never comes back.
- The email stays reserved: nobody else can sign up with it while the row exists.
