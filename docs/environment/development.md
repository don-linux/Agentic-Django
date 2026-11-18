# Development variables

Every variable the development setups read, with its purpose, whether it is required, and the file it lives in. Precedence and the production file: [README.md](README.md).

## Root `.env`

From `.env.example`. The dev Compose file passes it to the django, worker and Postgres containers (`env_file`), and `config/settings/local.py` reads it when Django runs on your machine ([without-docker.md](../development/without-docker.md)). Inside the containers Compose overrides the hosts, so the values below that name `localhost` serve only the run without Docker.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `DJANGO_DEBUG` | No | `True` (local settings) | Django's debug mode. |
| `DJANGO_TIME_ZONE` | No | `UTC` | Display and date-boundary zone; data is stored in UTC. |
| `DJANGO_SECRET_KEY` | No | A `django-insecure-` key | Only local settings accept the insecure default. |
| `POSTGRES_DB` | Yes | | Database name; the Postgres container creates it on first start. |
| `POSTGRES_USER` | Yes | | Database role (`django`, as in production). |
| `POSTGRES_PASSWORD` | Yes | | The role's local password. |
| `POSTGRES_HOST` | No | `postgres_db` | `localhost` without Docker; Compose sets `postgres_db` in the containers. |
| `POSTGRES_PORT` | No | `5432` | Compose sets `5432` in the containers. |
| `DJANGO_MAILER_HOST` | No | `mailpit` | The SMTP host: Mailpit. `localhost` without Docker. |
| `DJANGO_MAILER_PORT` | No | `1025` | Mailpit's SMTP port. |
| `VALKEY_URL` | No | `redis://localhost:6379` | The Valkey server, without credentials or a database number; Compose sets `redis://valkey:6379` in the containers. |
| `FRONTEND_URL` | No | `http://localhost:3000` | Base URL of the links in account emails. |
| `CORS_ALLOWED_ORIGINS` | No | Empty (no cross-origin caller) | The SPA's origin, so the browser may call `/api/` and `/_allauth/`. |
| `DJANGO_EMAIL_SIGNATURE_NAME` | No | `Project` | Name in the account-email signature. |
| `DJANGO_EMAIL_SUBJECT_PREFIX` | No | `[Project]` and a space | Prefix of every email subject. |
| `DJANGO_DEFAULT_FROM_EMAIL` | No | `Project <noreply@example.com>` | Sender of every email. |
| `DJANGO_API_MAX_PAGE_NUMBER` | No | `1000` | Deepest page a page-number list serves; deeper is a 404. |
| `DJANGO_LOG_SQL` | No | `False` | Print every SQL query in the `runserver` console. |
| `DJANGO_LOG_FORMAT` | No | `text` | Log lines: `text`, or `json` (one object per line). Production defaults to `json`. |

## `docker/dev/.env`

From `docker/dev/.env.example`. Compose reads it for interpolation only (`${VAR}` in `docker/dev/compose.yaml`); no container receives it.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `PROJECT_NAME` | No | `project-name` | Compose project name, and the `-dev-django` image name. Unique per checkout ([worktrees](../development/with-docker.md#worktrees)). |
| `DOCKER_DEV_DJANGO_PORT` | No | `8000` | Host port of the API, on `127.0.0.1`. |
| `DOCKER_DEV_POSTGRES_DB_PORT` | No | `5432` | Host port of Postgres; change it when the host runs its own. |
| `DOCKER_DEV_MAILPIT_UI_PORT` | No | `8025` | Host port of the Mailpit inbox. |
| `DOCKER_DEV_MAILPIT_SMTP_PORT` | No | `1025` | Host port of Mailpit's SMTP server. |
| `DOCKER_DEV_VALKEY_PORT` | No | `6379` | Host port of Valkey; change it when the host runs Valkey or Redis. |
| `HOST_UID` | No | `1000` | UID of the container user; set it to `id -u` and rebuild ([running as root](../development/with-docker.md#running-as-root)). |
| `HOST_GID` | No | `1000` | GID of the container user; set it to `id -g` and rebuild. |

## Not in the examples

Set them in the shell (or, where noted, in the root `.env`) only when needed.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `DJANGO_SETTINGS_MODULE` | No | `config.settings.local` for `manage.py` | The settings module ([settings.md#settings-module-default](../settings.md#settings-module-default)). The dev Compose file sets it on the worker, because `config/celery.py` defaults to production. |
| `DJANGO_ENV_FILE` | No | The root `.env` | The file `config/settings/local.py` reads; empty turns the read off. Shell only: the file cannot name itself. |
| `DJANGO_ALLOW_REMOTE_LOCAL` | No | `False` | `true` lets local settings use a database or Valkey that is not on loopback or a Compose service, such as a hosted development database. Shell or root `.env`. |
| `DATABASE_URL` | No | | A full database URL instead of the `POSTGRES_*` values. Shell or root `.env`. |

Every other variable that `config/settings/base.py` reads (Valkey databases and timeouts, the cache, Celery limits, Argon2, throttles, registration, `AXES_FAILURE_LIMIT`, the media paths) works in the root `.env` too, with the meaning and default given in [production.md](production.md).
