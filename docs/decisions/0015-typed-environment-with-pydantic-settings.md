# 0015. The environment is read through typed pydantic-settings classes

- Status: Accepted
- Date: 2026-11-22

## Context

The settings modules read the process environment with django-environ: `env("NAME", default=...)`, `env.bool(...)`, `env.list(...)` and `env.db(...)`. django-environ ships no type information, so the template kept its own stubs in `typings/environ/`. `stubtest` checked them against every release. [docs/typing.md](../typing.md) listed this as a permanent workaround. The variable names were plain strings in calls, so neither type checker knew which variables the settings read. `core/tests/test_env_reference.py` found them by matching call names.

django-environ also reads values in ways that hide mistakes:

- `env.bool` returns False for any word it does not know, an empty value included, and True for "ok".
- A value that starts with `$` is replaced by the variable it names. A generated secret that starts with `$` was lost. When the named variable was missing, the error message printed part of the secret.
- `env.list` keeps the spaces around items.

The Ninja migration left pydantic-settings for a later pull request, pinned to 2.16.3.

## Decision

Each settings module reads its variables through one pydantic-settings class in `config/settings/env.py`. `BaseEnv` is for `base.py`, and `LocalEnv`, `TestEnv` and `ProductionEnv` are for the modules of the same name.

- **What a class holds:** only what its own module reads. A variable that two modules read with different rules is declared in both.
- **Names:** a field is named exactly like its variable, and names are case-sensitive.
- **Errors:** `read_env()` turns every missing or invalid variable into one `ImproperlyConfigured`. It names each variable and never prints a value.
- **Guards:** `config/settings/guards.py` does not change. The guards still receive plain values.
- **Other parsers:** dj-database-url parses `DATABASE_URL`. Local settings load the root `.env` with python-dotenv, without `${VAR}` expansion.

The rules are in [docs/settings.md#environment-variables](../settings.md#environment-variables) and [AGENTS.md](../../AGENTS.md#security-email-and-files).

## Consequences

**What gets better:**

- pyright and mypy see the type of every value the settings read. `typings/`, `stubtest` and the workaround's row in docs/typing.md are gone.
- Every variable is a field. `core/tests/test_env_reference.py` reads the classes, and fails if a settings module reads `os.environ` directly.
- Startup is stricter. A boolean must be one of `true/false/1/0/yes/no/on/off/t/f/y/n`, list items are stripped, and every problem is reported at once.

**What changes:**

- Three runtime dependencies (pydantic-settings, python-dotenv and dj-database-url) replace django-environ. pydantic was already a dependency.
- A `DATABASE_URL` entry gains `CONN_MAX_AGE`, `CONN_HEALTH_CHECKS` and `DISABLE_SERVER_SIDE_CURSORS`, all at Django's defaults.

**What must not be undone without a new record:**

- Reading the environment by string lookups in settings.
- A `.env` source or a secrets directory on any class: production must never read a stray `.env` file.

## Alternatives

- **Keep django-environ and its stubs.** The stubs stay a permanent workaround, and so does the lax parsing.
- **One class per area** (database, Valkey, email). It is closer to the docs, but production would need a subclass per area to make its variables required. That means more classes, with no gain for someone reading one module.
- **Production guards as Pydantic validators.** Every guard's error type would change, and so would the tests that pin them. The guards already receive typed values.
- **Subclasses of `BaseEnv`.** pyright rejects a subclass field that drops the base field's default, and `VALKEY_URL` is required only in production. So each class stands alone, on a shared configuration base.
- **Replicate django-environ's parsing exactly.** That is more custom code, kept only to preserve behaviour that hides configuration mistakes.
