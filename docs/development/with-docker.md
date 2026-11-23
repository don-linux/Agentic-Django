# Development with Docker

The dev stack in `docker/dev/compose.yaml`: Django, a Celery worker, Postgres, Valkey and Mailpit, and Celery beat with the `beat` profile. The Dev Container in `.devcontainer/` reuses it.

## Start the stack

After the [shared first steps](README.md#shared-first-steps):

```bash
cp docker/dev/.env.example docker/dev/.env
docker compose -f docker/dev/compose.yaml up --build
```

The web and worker services build one image (`${PROJECT_NAME}-dev-django`; `project-name-dev-django` when `PROJECT_NAME` is unset) from `docker/dev/Dockerfile`. Both bind-mount the checkout with `:z`, so SELinux hosts (Fedora, RHEL) relabel it for the container; on other hosts the flag does nothing. Commands in the other docs run inside the container; from the host, prefix them with `docker compose -f docker/dev/compose.yaml exec django`.

## Ports

Every port is published on `127.0.0.1` only, and `docker/dev/.env` can change it:

- API: <http://localhost:8000> (`DOCKER_DEV_DJANGO_PORT`)
- Mailpit: <http://localhost:8025> (`DOCKER_DEV_MAILPIT_UI_PORT`). It catches every email, including verification codes.
- Mailpit SMTP: `localhost:1025` (`DOCKER_DEV_MAILPIT_SMTP_PORT`), for Django running on the host.
- Valkey: `localhost:6379` (`DOCKER_DEV_VALKEY_PORT`). The cache, the sessions and the task queue.
- PostgreSQL: `localhost:5432` (`DOCKER_DEV_POSTGRES_DB_PORT`).

If the host already uses one of these ports, for example for its own Postgres or Valkey/Redis, the stack fails to start with "port is already allocated". Change the matching `DOCKER_DEV_*_PORT` in `docker/dev/.env` (the example file shows 5433 and 6380 for Postgres and Valkey). The containers still reach each other on their own ports over the Compose network.

## Worker

The `celery_worker` service runs the Celery tasks ([../tasks.md](../tasks.md)). It does not reload code, so restart it after changing a task:

```bash
docker compose -f docker/dev/compose.yaml restart celery_worker
docker compose -f docker/dev/compose.yaml logs -f celery_worker
```

Compose sets `DJANGO_SETTINGS_MODULE=config.settings.local` only on the Celery services, because `config/celery.py` defaults to production. It starts Celery only once `migrate --check` finds nothing unapplied, so no task runs on an old schema; until then it logs `waiting for migrations`. It never migrates itself, because two `migrate` runs at once would race: the django service applies them on start, and in the Dev Container `postStartCommand` does. After you add a migration, run `migrate` before restarting the worker.

Periodic tasks need `celery_beat`, which starts only with its profile, as in production ([../tasks.md](../tasks.md#periodic-tasks-beat)). Run one: two beats send every periodic task twice.

```bash
docker compose -f docker/dev/compose.yaml --profile beat up -d celery_beat
```

## Valkey

The development Valkey is throwaway: no persistence and no password, with the same `volatile-lru` eviction as production.

## Dev Container

Open the folder in the Dev Container to work inside the same Compose stack. Before it starts, `initializeCommand` copies `.env.example` to `.env` and `docker/dev/.env.example` to `docker/dev/.env` on the host, each only if it is missing, so a fresh clone opens without manual steps and an existing file is never overwritten. `.devcontainer/compose.yaml` replaces the django command with `sleep infinity`, so the container stays idle until you start the VS Code launch configuration or run `python manage.py runserver 0.0.0.0:8000`. `.devcontainer/devcontainer.json` keeps that Compose command (`overrideCommand: false`), runs `python manage.py migrate --noinput` each time the container starts (`postStartCommand`), and connects as `django` (`remoteUser`; [Running as root](#running-as-root)).

## Running as root

The web and worker containers run as `django`, a user created with `HOST_UID` and `HOST_GID` from `docker/dev/.env` (1000 by default). On Linux, set them to the output of `id -u` and `id -g` and rebuild (`up --build`): files the container writes to the checkout, such as migrations, `uv.lock` or `docs/openapi.json`, are then yours. `/opt/venv` belongs to `django`, so `uv sync --locked` works in the container. The Dev Container connects as `django`, and on Linux `updateRemoteUserUID` gives that user your UID and GID. It does not re-own `/opt/venv`, so keep `HOST_UID` and `HOST_GID` right. Docker Desktop on macOS and Windows maps file ownership by itself, so the defaults work there.

For a one-off root shell, for example to try a package with `apt-get install`:

```bash
docker compose -f docker/dev/compose.yaml exec -u root django bash
```

What you change that way is lost when the container is recreated. Permanent system packages go in the `apt-get install` line of `docker/dev/Dockerfile`.

## Worktrees

The dev Compose file bind-mounts the checkout that contains it (`../../:/app`) and reads that checkout's untracked `.env` and `docker/dev/.env`, and `docker compose -f docker/dev/compose.yaml exec django ...` runs in whichever container is up for `PROJECT_NAME`. To run a second stack from another Git worktree, copy both env files into it, give its `docker/dev/.env` a unique `PROJECT_NAME` and free `DOCKER_DEV_*_PORT` values, and start that worktree's stack before running `exec` there.
