# 0014. User extends AbstractBaseUser and PermissionsMixin, not AbstractUser

- Status: Accepted
- Date: 2026-11-22

## Context

`User` logs in with its email and has a single `name` field. It used to extend `AbstractUser` and remove `username`, `first_name` and `last_name` by setting them to `None`, and its manager extended Django's `UserManager` with email-first `create_user` and `create_superuser`. Three problems came from that:

- The async creators inherited from Django's `UserManager`, `acreate_user` and `acreate_superuser`, still required `username` and failed with `TypeError` on every call. No test or type checker caught it.
- The removed fields and the narrower creator signatures needed six lines with type-checker suppressions.
- The manager overrode `UserManager._create_user`, an undocumented private helper of Django.

Django's guide covers this case. Its `UserManager` is meant for models whose fields match Django's default user, `username` included. Any other model "needs a custom manager that extends `BaseUserManager`" ([Writing a manager for a custom user model](https://docs.djangoproject.com/en/6.1/topics/auth/customizing/#writing-a-manager-for-a-custom-user-model)).

## Decision

`User` extends `AbstractBaseUser` and `PermissionsMixin`, and declares `is_staff`, `is_active` and `date_joined` itself, with `AbstractUser`'s exact options. `UserManager` extends `BaseUserManager` and implements `create_user`, `create_superuser`, `acreate_user` and `acreate_superuser`, all taking the email first. The rule is in [AGENTS.md](../../AGENTS.md#data) and [ARCHITECTURE.md](../ARCHITECTURE.md#layers).

## Consequences

**What stays the same:**

- The database schema and migrations. The fields, their order in `_meta.fields`, the options, the constraint and the serialized manager are unchanged, so `makemigrations --check` reports nothing.
- Existing sessions. The session hash depends only on the password hash and a fixed salt.
- `USER_CACHE_VERSION`. A cached user is a pickle of its field values, and the field set is the same, so pickles written before this change still load.
- `create_user` and `create_superuser` keep their signatures and error messages.

**What gets better:**

- `acreate_user` and `acreate_superuser` work.
- The six suppressed lines are gone.
- The manager no longer overrides a private Django method.

**What is gone:**

- `user.username`, `user.first_name` and `user.last_name` raise `AttributeError` instead of returning `None`. allauth checks with `hasattr`, so it is unaffected (`ACCOUNT_USER_MODEL_USERNAME_FIELD = None`).
- `User.email_user`, `User.username_validator` and `UserManager.with_perm`. Nothing in the template used them.

**What to keep in mind:**

- The async creators hash the password on the event loop, as Django's own `UserManager` does. `_build_user` must stay free of queries.
- `is_active` is annotated with the union that django-stubs gives `AbstractBaseUser.is_active` ([typing.md](../typing.md#model-field-annotations)).
- Do not switch `User` back to `AbstractUser` without a new record: the async creators would break again.

## Alternatives

- **Keep `AbstractUser` and also override `acreate_user` and `acreate_superuser`:** rejected. Every override needs two more suppressions, the `= None` lines stay, and the model keeps depending on Django's `username`-shaped manager.
- **Override `UserManager._create_user_object`:** rejected. It is private and undocumented.
- **Also inherit `core.models.PublicIdModel`:** rejected. It would move `public_id` in `_meta.fields` and add a third `Meta` parent, for no gain; `User` declares `public_id` itself ([0003](0003-public-uuidv7-ids.md)).
