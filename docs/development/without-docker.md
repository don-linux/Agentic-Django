# Development without Docker

Django runs directly on your machine with `config.settings.local`. You run PostgreSQL, Valkey and an SMTP catcher yourself; the steps that do not depend on the setup are in [README.md](README.md) and [../development.md](../development.md).

## Requirements

- Python 3.14 and [uv](https://docs.astral.sh/uv/).
- A C compiler and the PostgreSQL client headers, because `psycopg[c]` builds against libpq (`pg_config` must be on the `PATH`). On Debian or Ubuntu: `sudo apt install build-essential libpq-dev`.
- PostgreSQL 18, with a role and a database for the project.
- Valkey (or another server that speaks the Redis protocol) on `localhost:6379`, without a password.
- An SMTP catcher such as [Mailpit](https://mailpit.axllent.org/) on `localhost:1025`, with its inbox on <http://localhost:8025>.

## Set up

1. Do the [shared first steps](README.md#shared-first-steps). `.env.example` already points at `localhost` for PostgreSQL, Valkey and the SMTP catcher; adjust `POSTGRES_*`, `VALKEY_URL` and `DJANGO_MAILER_*` in `.env` if yours differ. `manage.py` uses `config.settings.local`, which reads that file ([../settings.md#env-files](../settings.md#env-files)); a variable exported in your shell wins over it.
2. Install the dependencies into `.venv`:

   ```bash
   uv sync --locked
   ```

3. Create the tables and the demo users:

   ```bash
   uv run python manage.py migrate
   uv run python manage.py seed_dev
   ```

## Run

```bash
uv run python manage.py runserver 127.0.0.1:8000
```

The API answers on <http://localhost:8000>; check it with `curl http://localhost:8000/health/`. The account flow, `seed_dev` users and Bruno are in [../development.md](../development.md#accounts).

## Checks

`scripts/check.sh` calls `python`, `ruff` and `mypy` from the virtualenv, and the test settings never read `.env` themselves ([../settings.md#test](../settings.md#test)). Run it through `uv` with the root `.env` loaded, so the tests find PostgreSQL and Valkey:

```bash
uv run --env-file .env scripts/check.sh
```

`check.sh` with no argument runs `lint` then `test`; pass `lint`, `test` or `reversible` for one part ([../development.md#checks](../development.md#checks)). The tests create and drop `test_<POSTGRES_DB>`, so the role needs `CREATEDB` (`ALTER ROLE <user> CREATEDB;` as a superuser). Regenerate the schema the same way:

```bash
uv run --env-file .env python manage.py spectacular --settings config.settings.test --file docs/openapi.yaml --validate --fail-on-warn
```

## Celery worker

Tasks need a worker. The `celery` command has no `manage.py` to pick the settings, and `config/celery.py` defaults to production, so set the module yourself:

```bash
DJANGO_SETTINGS_MODULE=config.settings.local uv run celery --app config worker --loglevel INFO --concurrency 1
```

It does not reload code; restart it after changing a task.
