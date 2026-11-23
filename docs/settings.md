# Settings

Reference for how the settings modules are chosen and composed. Area policy (cache, security, email, tasks, API errors) stays in its own doc; this page only links to it.

## Env files

Every settings module reads the process environment through its class in `config/settings/env.py` ([Environment variables](#environment-variables)). Who fills that environment depends on the module:

| Module | Env file it reads |
| --- | --- |
| `config.settings.local` | The root `.env`, loaded into the process environment by `config/settings/_env_file.py` (python-dotenv) before `base.py` runs. Values are taken as written: `${OTHER}` is not expanded. `DJANGO_ENV_FILE` points at another file; an empty value turns the read off. The `ENV_FILE` setting shows which file was loaded. |
| `config.settings.test` | The process environment, for the database and Valkey connection only. Dev Compose injects the root `.env` into the containers, so `test.py` pins every other value the tests depend on (time zone, frontend URL, CORS, email identity, registration, page depth); `core/tests/test_test_settings.py` checks that a project `.env` cannot change them. |
| `config.settings.production` | None. Compose passes `docker/prod/.env` to the containers through `env_file`, and the image contains no `.env`. Compose also sets `DJANGO_SETTINGS_MODULE` under `environment:`, which wins over that file, and the start scripts refuse any other module. |

A variable already set in the process environment always wins over the file, so under Docker the values Compose passes win, and on a host a variable exported in the shell wins. Never put `DJANGO_SETTINGS_MODULE` in a `.env` file.

## Environment variables

`config/settings/env.py` declares every variable the settings read, with its type and default. There is one class per settings module: `BaseEnv` for `base.py`, and `LocalEnv`, `TestEnv` and `ProductionEnv` for the modules of those names. A class lists only what its own module reads. A variable that two modules read with different rules is in both classes:

- `DJANGO_DEBUG` defaults to `True` only in `LocalEnv`.
- `VALKEY_URL` has a localhost default in `BaseEnv` but is required in `ProductionEnv`.

Each module reads its class once, at the top: `_env = read_env(ProductionEnv)`.

- A missing required variable, or a value of the wrong type, stops the import with `ImproperlyConfigured`.
- The message names every such variable at once and never prints a value, for example "Set the DJANGO_SECRET_KEY environment variable." or "DJANGO_DEBUG: Input should be a valid boolean...".

How a value is read:

- A field is named exactly like its variable, and names are case-sensitive.
- Booleans take `true`, `false`, `1`, `0`, `yes`, `no`, `on`, `off`, `t`, `f`, `y` and `n`, in any case. Anything else is refused, an empty value included.
- Lists are comma-separated (`DJANGO_ALLOWED_HOSTS=api.example.com,www.example.com`). Each item is stripped and empty items are dropped. A list is never JSON.
- A value is used as written: `$NAME` is not replaced by another variable.
- An empty value is still a value, so the production guards can refuse an empty password or mail host. The exception is a field that is `None` when unset, which reads an empty value as unset too: the S3 region, endpoint and custom domain, `DJANGO_MAILER_PORT` and `DATABASE_URL`.
- No class reads a `.env` file or a secrets directory. Only `config.settings.local` loads the root `.env`, into the process environment, before `base.py` runs ([Env files](#env-files)).

To add a variable:

1. Add a field to the class of the module that reads it, with its type and default. A field with no default is required.
2. Read it as `_env.NAME`.
3. List it in its `.env.example` and in `docs/environment/`; `core/tests/test_env_reference.py` checks both.
4. Pin it in `test.py` if a project value could change what a test sees ([Test](#test)).

Never read `os.environ` in a settings module: `core/tests/test_env_reference.py` fails.

## Settings module default

| Entry point | Module when `DJANGO_SETTINGS_MODULE` is unset |
| --- | --- |
| `manage.py` | `config.settings.local` |
| pytest | `config.settings.test` (`--ds` in `pyproject.toml`) |
| `scripts/check.sh` | `config.settings.test`, unless the shell already exports another module, which it keeps |
| `config/wsgi.py`, `config/celery.py` | `config.settings.production` |

`config/wsgi.py` and `config/celery.py` must agree: the worker imports `config/celery.py` first. Celery installs its Django integration only when `DJANGO_SETTINGS_MODULE` is set, so `config/celery.py` sets it before it builds the app. `config/__init__.py` imports that module first, so a different default there would silently replace the one in `config/wsgi.py`; `manage.py` and pytest set their own module before importing it. The dev Compose file sets `config.settings.local` only on the worker service, because the web service runs `manage.py`, which already defaults to it.

## Local

`config/settings/local.py` refuses a remote database or Valkey: `DATABASE_URL`, `POSTGRES_HOST`, `VALKEY_URL` and `CELERY_BROKER_URL` must point at a loopback address or a Compose service name (`postgres_db`, `valkey`), whether the value comes from the shell or from `.env`, so `manage.py` cannot run development settings against production. `DJANGO_ALLOW_REMOTE_LOCAL=true` allows a hosted development database. It also turns `DEBUG` on by default (`DJANGO_DEBUG`) and accepts an insecure default `SECRET_KEY` that production refuses. Mail goes to Mailpit. `whitenoise.runserver_nostatic` comes first in `INSTALLED_APPS`, so `runserver` leaves static files to WhiteNoise as production does. It adds django-extensions, logs N+1 queries instead of failing ([development.md#n-plus-one](development.md#n-plus-one)), and can print SQL ([development.md#sql-log](development.md#sql-log)).

## Test

`config/settings/test.py`:

- Caches are in memory (`LocMemCache`, key prefix `test`), and `conftest.py` empties them before each test. Tests never reach a real Valkey, except the suite marked `valkey` (`core/tests/test_valkey_integration.py` and `core/tests/ninja/test_valkey_integration.py`), which uses the Valkey at `VALKEY_URL` on its own databases and key prefix.
- Tasks run inline (`CELERY_TASK_ALWAYS_EAGER`, errors propagate) and the broker is `memory://`. Eager mode exists only here; production refuses it.
- `axes.W001` is silenced: `LocMemCache` is per process, which is fine for one test process. Every other environment uses Valkey, where the check stays on.
- Passwords hash with MD5 for speed, mail uses the in-memory backend, and `WHITENOISE_AUTOREFRESH` is on.
- Throttles stay on; a test that needs other rates patches them ([api.md#throttling](api.md#throttling)).
- Project values are pinned to the template defaults: `TIME_ZONE`, `FRONTEND_URL`, `CORS_ALLOWED_ORIGINS`, the email identity (`DEFAULT_FROM_EMAIL`, subject prefix, signature), `ACCOUNT_ALLOW_REGISTRATION` and `API_MAX_PAGE_NUMBER`. A new variable in `.env.example` is pinned here too, or listed as harmless in `core/tests/test_test_settings.py`.

## Production overrides

`config/settings/production.py`:

- Requires `VALKEY_URL` and `VALKEY_PASSWORD` (`ProductionEnv`). `base.py` has already built `CACHES` from them, but with a localhost default and an empty password that production must never fall back to.
- Never assigns eager task execution. The guards read whatever the module ended up with, so a value inherited from `base.py` or the environment is still refused.
- Calls the startup guards in `config/settings/guards.py` last, so the container refuses example or weak values ([security.md#production-secrets](security.md#production-secrets)).

## Logging

`DJANGO_LOG_FORMAT` picks `json` (one object per line) or `text`. Celery must not take over logging (`CELERY_WORKER_HIJACK_ROOT_LOGGER = False`), and both the `celery` and the `celery.task` loggers need the handler, or task log lines are lost. The JSON formatter drops Celery's `data` attribute, which would attach task arguments to Celery's own log lines; the line already carries `task_id` and `task_name`.

Request ids and `APP_RELEASE` in log lines: [ARCHITECTURE.md#deployment](ARCHITECTURE.md#deployment). Printing SQL: [development.md#sql-log](development.md#sql-log). What error reports hide: [security.md#exception-reports](security.md#exception-reports).

## Templates

`TEMPLATES` finds only the allauth email overrides in `templates/` and keeps the context processors the admin needs; Swagger UI builds its own page. The file contract for those emails is in [email.md#templates](email.md#templates).
