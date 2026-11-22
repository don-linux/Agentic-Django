# Deploy

Running the production Compose stack (`docker/prod/compose.yaml`). The topology is in [ARCHITECTURE.md#deployment](ARCHITECTURE.md#deployment); the host checklist is [NEXT-STEPS.md](NEXT-STEPS.md).

## Start

The production stack is Django (Gunicorn and WhiteNoise), a Celery worker, PostgreSQL and Valkey. TLS stays out of it ([TLS](#tls)). First deploy, from the repository root on the host:

```bash
cp docker/prod/.env.example docker/prod/.env   # then fill it (Env)
sudo mkdir -p /srv/project-name/uploads/{public,private} && sudo chown -R 1000:1000 /srv/project-name/uploads
APP_RELEASE=$(git rev-parse --short HEAD) docker compose -f docker/prod/compose.yaml up -d --build
docker compose -f docker/prod/compose.yaml exec django python manage.py createsuperuser
```

`docker/prod/start.sh` first exits 1 unless `DJANGO_SETTINGS_MODULE` is `config.settings.production`, then runs `manage.py check --deploy --fail-level ERROR` (importing the settings already refuses example secrets), checks that the upload folders are writable, applies migrations unless told not to ([Migrate](#migrate)), and starts Gunicorn. The level is ERROR so that a deploy warning cannot crash-loop a restarting container. The upload checks run inside `if`, so their message still prints under `set -e`. `docker/prod/start-worker.sh` does the same two checks, runs no migrations, and starts the Celery worker.

## Env

Copy `docker/prod/.env.example` to `docker/prod/.env` and replace every `change-me`, the domain and the SMTP values. Production refuses to start while a secret still has its example value; [security.md#production-secrets](security.md#production-secrets) lists the rules and how to rotate `DJANGO_SECRET_KEY`. Sensitive fields (national IDs, tax IDs, tokens) can be encrypted with dedicated keys: [security.md#recipe-encrypting-sensitive-fields](security.md#recipe-encrypting-sensitive-fields).

Mail must work before anyone can sign up: production refuses to start until `DJANGO_MAILER_HOST`, `DJANGO_DEFAULT_FROM_EMAIL` and `FRONTEND_URL` are real ([email.md](email.md)). Test it with:

```bash
docker compose -f docker/prod/compose.yaml exec django python manage.py sendtestemail you@example.com
```

## Uploads

Uploads live outside the repository, in `DOCKER_PROD_DJANGO_UPLOADS_PATH` (default `/srv/project-name/uploads`), with `public` and `private` folders owned by uid 1000, the image's user. The `mkdir` and `chown` are in [Start](#start); the host proxy serves `public` ([Proxy](#proxy)), and [NEXT-STEPS.md#the-uploads-folder](NEXT-STEPS.md#the-uploads-folder) has the checklist.

## Image

One image runs the web process and the worker. Its name carries no release tag, so `docker compose run` and `up` find what `build` produced.

- Both stages of `docker/prod/Dockerfile` build on one `FROM ... AS base`, pinned by tag and digest, because Dependabot only updates the first `FROM` ([ci.md#image-pins](ci.md#image-pins)).
- The image installs Debian security updates published since the base image was built and removes pip: nothing installs packages at runtime, and pip's vendored libraries would only add findings to the image scan.
- `collectstatic` runs at build time with throwaway values that never reach the running container; they must still pass the production guards.
- The release metadata (`APP_RELEASE` and the rest of `APP_*`) is declared after `collectstatic`, so a new value on every commit does not invalidate the cached layers above.

Static files are collected when the image is built. `APP_RELEASE` stamps it with the deployed commit ([ARCHITECTURE.md#deployment](ARCHITECTURE.md#deployment)); without it the release is `dev`.

## Settings module

The settings module production runs with. The reference is [settings.md#production-overrides](settings.md#production-overrides).

## Migrate

`docker/prod/migrate.sh` applies the migrations. `start.sh` runs it on every start unless `DJANGO_MIGRATE_ON_START=false`. With more than one django replica, set it to `false` and run it once per deploy, so replicas never race to migrate:

```sh
docker compose -f docker/prod/compose.yaml run --rm django sh /app/docker/prod/migrate.sh
```

Migrations may run longer than a request, so they run without the statement timeout, but they still must not wait forever on a lock.

To migrate once per deploy with the new image:

```bash
APP_RELEASE=$(git rev-parse --short HEAD) docker compose -f docker/prod/compose.yaml build
docker compose -f docker/prod/compose.yaml run --rm django sh /app/docker/prod/migrate.sh
docker compose -f docker/prod/compose.yaml up -d
```

## Gunicorn

Every option is in `docker/prod/gunicorn.conf.py`:

- The `timeout` restarts a worker that stops heartbeating; with threads it is not a per-request limit. PostgreSQL's statement timeout bounds each query.
- `graceful_timeout` is the time in-flight requests get after SIGTERM. Keep it below `stop_grace_period` in `docker/prod/compose.yaml` (30 s), or Docker kills workers mid-request.
- A worker is recycled after about 1000 requests, jittered so workers do not restart together: it caps slow memory growth.
- The heartbeat file goes to `/dev/shm`, a tmpfs, not the image's disk. The control socket is off: nothing uses it, and it needs a writable `$HOME`.
- The access log shows the client from `X-Forwarded-For` (the whole chain, including entries a client forged), because the peer is always the Docker gateway, plus the response's request id. `forwarded_allow_ips` stays at its default: Django reads `X-Forwarded-Proto` itself.

## Proxy

Gunicorn must only be reachable through the host proxy. Production trusts `X-Forwarded-Proto` (`SECURE_PROXY_SSL_HEADER`) and the last `X-Forwarded-For` hop (`DJANGO_TRUSTED_PROXY_COUNT`, default 1) from whoever connects to Gunicorn, so Compose publishes it on `127.0.0.1` only (`DOCKER_PROD_DJANGO_BIND`).

Never set `DOCKER_PROD_DJANGO_BIND` to `0.0.0.0` or a public address. A client that reaches Gunicorn directly can fake HTTPS, spoof its IP past throttles, rate limits and the admin lockout, and skip the proxy's request body limit.

The host proxy has to:

- Send `X-Forwarded-Proto: https` (without it every request redirects to HTTPS in a loop) and `X-Forwarded-For` (used by allauth rate limits and API throttling). `DJANGO_TRUSTED_PROXY_COUNT` must equal the number of proxies that append to `X-Forwarded-For` (default `1`, for a single nginx).
- Serve `/media/` from `<DOCKER_PROD_DJANGO_UPLOADS_PATH>/public`.
- Check `/health/` before sending traffic ([Health](#health)).

The nginx configuration is in [NEXT-STEPS.md#nginx](NEXT-STEPS.md#nginx).

## TLS

TLS ends at a proxy on the host (Caddy, nginx) in front of `127.0.0.1:8000`; the stack itself serves plain HTTP ([NEXT-STEPS.md#nginx](NEXT-STEPS.md#nginx)).

## Worker

The stack starts the Celery worker (`celery_worker`) once Django is healthy. It is on by default, because a task enqueued while the worker is off would wait forever without any error. A project that has no tasks yet may stop it, and periodic tasks need the optional `beat` profile ([tasks.md](tasks.md)):

```bash
docker compose -f docker/prod/compose.yaml up -d --scale celery_worker=0   # without the worker
docker compose -f docker/prod/compose.yaml --profile beat up -d            # worker and beat
```

## Health

The proxy requests `http://127.0.0.1:8000/health/` (readiness: it queries the database and pings Valkey) and stops sending traffic unless the response is 200. `/health/live/` answers without either, for restart-on-failure probes. Docker marks the container unhealthy when that check fails, but it does not close the port or restart Django. PostgreSQL and Valkey restart if their process exits; Django stays up and reports 200 again once both answer. The probe design is in [ARCHITECTURE.md#deployment](ARCHITECTURE.md#deployment).

## Capacity

Size the containers from the server's RAM before going live.

- **RAM** ≈ `workers × ~90 MB` + `workers × threads × DJANGO_ARGON2_MEMORY_COST_KIB` (the peak, when every thread hashes a password at once, as during a login burst) + PostgreSQL. With the defaults (3 workers × 3 threads, 100 MiB per hash) that is about 1.2 GB for Django alone. On a 1–2 GB VPS, lower `DJANGO_ARGON2_MEMORY_COST_KIB` to 19456 and `DJANGO_ARGON2_PARALLELISM` to 1 (the OWASP minimum), or lower the threads (`DOCKER_PROD_DJANGO_GUNICORN_THREADS`). Existing hashes are upgraded on each user's next login.
- **Memory limit:** the container's memory limit must stay above that peak, or a login burst gets the container killed. `docker/prod/compose.yaml` sets it from `DOCKER_PROD_DJANGO_MEM_LIMIT` (1536m by default), and the 256 MB `/tmp` tmpfs counts toward it while large uploads spool there.
- **The worker:** add `CELERY_WORKER_CONCURRENCY × CELERY_WORKER_MAX_MEMORY_PER_CHILD` (2 processes × about 195 MB by default) plus the parent process. Its container limit is `DOCKER_PROD_CELERY_WORKER_MEM_LIMIT` (768m). A project with no tasks yet can stop it ([tasks.md](tasks.md#the-worker-in-production)).
- **Valkey:** add `VALKEY_MAXMEMORY` (256 MB by default) twice. Rewriting the append-only file forks the process, and for a moment both copies exist, which is why `DOCKER_PROD_VALKEY_MEM_LIMIT` is 512m. When it reaches `VALKEY_MAXMEMORY`, Valkey evicts cache entries and keeps the task queue; if the queue alone fills it, new tasks are refused and logged.
- **Total:** RAM ≈ django + worker + 2 × `VALKEY_MAXMEMORY` + PostgreSQL. With the defaults that is about 1.2 GB + 0.5 GB + 0.5 GB + PostgreSQL.
- **Connections:** `workers × threads × replicas + CELERY_WORKER_CONCURRENCY`, plus a margin for migrations and `psql`, must stay below PostgreSQL's `max_connections` (100 by default). With `CONN_MAX_AGE` each thread keeps its connection open, and so does each worker process. Valkey needs no tuning: every thread and worker process holds a connection or two, a few dozen in all, against a limit of 10,000.
- **Workers and threads:** start with `2 × vCPU + 1` workers, capped by RAM, and 2–4 threads for this database-bound API. Gunicorn's `--preload` saves roughly 10–15% memory but complicates reloads; it is optional. With it, no module may call the cache or enqueue a task while it is imported: the connection would be opened before the fork and shared by every worker.
- **Kernel setting for Valkey:** set `vm.overcommit_memory = 1` on the host, in `/etc/sysctl.conf`, and apply it with `sudo sysctl vm.overcommit_memory=1`. Valkey warns about it when it starts: without it, the fork that rewrites the append-only file can fail when memory is short. A container cannot set it; it belongs to the host's kernel.

The Argon2 hasher keeps the algorithm name `argon2`, so existing hashes still verify after a cost change and `must_update()` rehashes them on the next login. The cost is read from settings on every call.

## Upgrading from the database cache

Only for a deployment created before Valkey became the cache. Earlier versions of this template cached in PostgreSQL (`DatabaseCache`, table `django_cache`). A deployment created from one of them moves to Valkey like this:

1. Add `VALKEY_PASSWORD` to `docker/prod/.env`: 32 characters or more, generated with the command shown in `docker/prod/.env.example`.
2. Deploy as usual. Compose starts the new `valkey` and `celery_worker` services, and django refuses to start without the password.
3. Drop the old table, which nothing reads any more:

   ```bash
   docker compose -f docker/prod/compose.yaml exec postgres_db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "DROP TABLE IF EXISTS django_cache;"'
   ```

Throttles and rate-limit counters start from zero. Nobody is signed out: sessions were already in PostgreSQL, and they are read from there until Valkey has them.
