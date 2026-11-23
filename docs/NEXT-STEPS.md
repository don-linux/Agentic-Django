# Next steps

The checklist after you copy this template, in order: personalize it, work on it locally, let CI check it, then go live on a Linux host. Each step links to the doc that explains it.

## After the copy

1. **Personalize the placeholders.** Run the `personalize-django-template` skill. `project`, `Project`, `project-name`, `example.com` and `project_db` still appear in settings, Compose, email and the schema ([development.md#personalize](development.md#personalize)).
2. **Bring the stack up**, with Docker or without it ([development/](development/README.md)). With Docker, the dev containers run as a non-root user, `django`, with `HOST_UID` and `HOST_GID` from `docker/dev/.env` (1000 by default). On Linux, if `id -u` or `id -g` prints something else, set them first. After changing them, or in a checkout whose old dev image ran as root, rebuild with `docker compose -f docker/dev/compose.yaml up -d --build` and give back the caches those root containers left ([with-docker.md#running-as-root](development/with-docker.md#running-as-root)).
3. **Install the Git hooks** on the host ([development.md#pre-commit](development.md#pre-commit)).
4. **Try the account flow.** Sign up, read the code in Mailpit, verify, and run the Bruno smoke requests after `seed_dev` ([development.md#accounts](development.md#accounts), [development.md#bruno](development.md#bruno)).
5. **Write your domain invariants** in `AGENTS.md`, with the product owner, before creating domain apps.

## CI

1. **Push and let CI run.** `.github/workflows/ci.yml` runs on pull requests and pushes to `main`. It needs no GitHub Actions secrets: the `secrets` job is a gitleaks scan of the history ([ci.md](ci.md)). Never commit `docker/prod/.env`.
2. **Protect `main`.** Mark the CI jobs as required checks ([ci.md#required-checks](ci.md#required-checks)).

## Before the first production `up`

1. **Copy the production env and fill it** ([Production secrets](#production-secrets), [deploy.md#env](deploy.md#env)).
2. **Create the uploads folder** ([The uploads folder](#the-uploads-folder)).
3. **Size the host** from its RAM and set `vm.overcommit_memory = 1` ([Capacity](#capacity)).
4. **Synchronize the clock** ([Server clock](#server-clock)).
5. **Keep `/admin/` off the public internet** if you can: the optional allow-list in [Nginx](#nginx). The admin login is a password plus a lockout ([security.md#admin-lockout](security.md#admin-lockout)).

## First production `up`

1. **Start the stack and create a superuser** ([deploy.md#start](deploy.md#start)).
2. **Put the TLS proxy in front** of `127.0.0.1:8000` ([Nginx](#nginx)).
3. **Prove that email goes out** with `sendtestemail` ([deploy.md#env](deploy.md#env), [email.md#checklist](email.md#checklist)).
4. **Ramp HSTS** once HTTPS works on every page ([security.md#hsts](security.md#hsts)).
5. **Schedule `clearsessions` and the database dumps** ([Expired sessions](#expired-sessions), [Backups](#backups)).
6. **Watch slow queries** on real data ([Slow queries](#slow-queries)).
7. **With more than one django replica**, migrate once per deploy ([deploy.md#migrate](deploy.md#migrate)).

---

## Nginx

Install nginx on the host and point the site at that port. `/media/` is read from the folder; everything else is proxied. `X-Forwarded-Proto` has to be `https`, or Django redirects every request and the browser loops.

Replace `example.com`, the certificate paths and the `alias` path.

```nginx
server {
    listen 80;
    server_name example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name example.com;
    server_tokens off;

    ssl_certificate     /etc/letsencrypt/live/example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/example.com/privkey.pem;

    # Hard cap per request; keep it >= the largest per-field upload limit. nginx
    # answers 413 itself, so the body is JSON like every other API error.
    client_max_body_size 20m;
    error_page 413 = @request_too_large;
    location @request_too_large {
        default_type application/json;
        return 413 '{"code":"request_too_large","detail":"Request body is larger than the server accepts."}';
    }

    location /media/ {
        alias /srv/project-name/uploads/public/;
        add_header X-Content-Type-Options "nosniff" always;
        add_header Content-Security-Policy "default-src 'none'; img-src 'self'; sandbox" always;
        add_header X-Frame-Options "DENY" always;
        # Safe only because stored names are random UUIDs (core/files.py): a new
        # upload never reuses a URL.
        add_header Cache-Control "public, max-age=2592000, immutable" always;

        # Files a browser could run (HTML, SVG, XML, JS) are only ever downloaded.
        # add_header here replaces the outer ones, so all of them are repeated.
        location ~* \.(html?|svg|xml|js)$ {
            add_header Content-Disposition "attachment" always;
            add_header X-Content-Type-Options "nosniff" always;
            add_header Content-Security-Policy "default-src 'none'; sandbox" always;
            add_header X-Frame-Options "DENY" always;
            add_header Cache-Control "public, max-age=2592000, immutable" always;
        }
    }

    # Private files: reachable only through a Django response with X-Accel-Redirect,
    # after the view checked permissions. A direct request is a 404.
    location /protected-media/ {
        internal;
        alias /srv/project-name/uploads/private/;
        default_type application/octet-stream;
        add_header X-Content-Type-Options "nosniff" always;
        add_header Content-Security-Policy "default-src 'none'; sandbox" always;
        add_header Cache-Control "private, no-store" always;
    }

    # Health probes are for local checks only; the internet gets a 403.
    location ^~ /health/ {
        allow 127.0.0.1;
        deny all;
        proxy_pass http://127.0.0.1:8000;
    }

    # JSON API responses are compressed here, not by gunicorn's workers.
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Request-ID $request_id;
        proxy_connect_timeout 5s;
        proxy_read_timeout 35s;
        gzip on;
        gzip_types application/json application/problem+json;
        gzip_min_length 1024;
        gzip_proxied any;
        gzip_comp_level 5;
        gzip_vary on;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        # Django keeps this id in its logs, the response, and error reports.
        proxy_set_header X-Request-ID $request_id;
        proxy_connect_timeout 5s;
        # Just above PostgreSQL's 30 s statement timeout (see below).
        proxy_read_timeout 35s;
    }
}
```

`$proxy_add_x_forwarded_for` appends the client IP to whatever `X-Forwarded-For` the client sent, so Django trusts only the last `DJANGO_TRUSTED_PROXY_COUNT` entries (default `1`: this nginx). Raise it if another proxy or load balancer that also appends sits in front; a wrong value lets clients forge their IP or puts every client in one throttle bucket.

Leave `proxy_request_buffering` on (the default): nginx buffers slow uploads, so they do not hold gunicorn threads.

nginx serves `/media/` itself, so Django's security headers never reach those files, and uploads share the origin of the admin and Swagger. Without the headers above, an uploaded `.html` or `.svg` would run as a page on that origin (stored XSS against staff). For untrusted public uploads, prefer a separate media host such as `media.example.com`.

API lists compress about 9:1, the cheapest latency win on mobile networks. `/_allauth/` stays uncompressed because its bodies carry the session token and echo user input, which compression would expose to a BREACH-style attack. Django's `GZipMiddleware` is not used: it would spend gunicorn worker CPU.

Timeouts cascade from the inside out. PostgreSQL cancels a query after 30 s (`POSTGRES_STATEMENT_TIMEOUT_MS`), nginx answers 504 after 35 s (`proxy_read_timeout`), and on shutdown gunicorn lets in-flight requests finish for 25 s (`graceful_timeout`) within Docker's 30 s `stop_grace_period`. Gunicorn's `timeout` (30 s) only restarts a worker that stops responding: with threads it does not limit a request, so after a 504 Django may still finish that request.

`/srv/project-name/uploads/public/` is the folder Docker mounts at `/app/uploads/public` (`DJANGO_MEDIA_ROOT`). A request to `https://example.com/media/photo.jpg` is the file `photo.jpg` in that folder. The checkout can live anywhere (even under `/home/<user>`, which `www-data` cannot traverse): only the uploads folder must be readable by nginx. `uploads/private/` is served only through the `internal` `/protected-media/` location; never put it under the `/media/` alias.

The admin login locks a username for 15 minutes after 5 failed passwords from one IP (django-axes). To also hide the admin from everyone outside your network, add a location for `DJANGO_ADMIN_URL` above `location /`:

```nginx
    # Optional: replace staff-path/ with DJANGO_ADMIN_URL and the ranges with yours.
    location /staff-path/ {
        allow 203.0.113.0/24;
        deny all;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Request-ID $request_id;
        proxy_connect_timeout 5s;
        proxy_read_timeout 35s;
    }
```

Use `GET http://127.0.0.1:8000/health/` (readiness) if the proxy should stop sending traffic while the database or Valkey is down. A healthy response is `200` and the body `ok`. `GET /health/live/` (liveness) answers `200 ok` without touching either: point a restart-on-failure probe at it, never at `/health/`, so an outage of the database or of Valkey does not restart Django in a loop. Both are reachable only from the host itself (the `location ^~ /health/` block above).

## The uploads folder

Create it before the first `up`, outside the repo, and give it to the user the container runs as (UID and GID 1000; `DOCKER_PROD_USER_UID` and `DOCKER_PROD_USER_GID` change them at build time):

```bash
sudo mkdir -p /srv/project-name/uploads/public /srv/project-name/uploads/private
sudo chown -R 1000:1000 /srv/project-name/uploads
```

It is a bind mount of a host folder, not a named volume: a `git clean -fdx`, a fresh clone or an `rsync --delete` deploy never touches it, and the host nginx can read it (it cannot read `/var/lib/docker/volumes`). Compose refuses to start if `DOCKER_PROD_DJANGO_UPLOADS_PATH` is unset or the folder does not exist, and the container exits with the `chown` command to run if it cannot write there. Commands that read the compose file without the stack's environment need it too: `docker compose -f docker/prod/compose.yaml --env-file docker/prod/.env config`.

Modes and the public/private layout: [files.md#permissions](files.md#permissions), [files.md#public-or-private](files.md#public-or-private).

## Production secrets

`docker/prod/.env.example` ships with `change-me`. Copy it to `docker/prod/.env` and replace these before exposing the site:

- `DJANGO_SECRET_KEY`, from `python -c "import secrets; print(secrets.token_urlsafe(50))"`.
- `DJANGO_ADMIN_URL`, a hard-to-guess path with a trailing slash.
- `POSTGRES_PASSWORD`. No minimum length is enforced; use a long random one, from `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- `VALKEY_PASSWORD`, from `python -c "import secrets; print(secrets.token_urlsafe(32))"`. It never goes inside `VALKEY_URL`.
- `DJANGO_MAILER_HOST`, plus the username and password your SMTP server requires ([email.md](email.md) has provider values, DNS records and a checklist).
- `DJANGO_DEFAULT_FROM_EMAIL`, an address on a domain verified with your provider.
- `FRONTEND_URL`, the real frontend, used in email links.
- `DJANGO_ALLOWED_HOSTS`, the API host.
- `DJANGO_CSRF_TRUSTED_ORIGINS` and `CORS_ALLOWED_ORIGINS`, the frontend origin (the same origin as `FRONTEND_URL`), not the API host.
- `DJANGO_TRUSTED_PROXY_COUNT`, the number of proxies that append to `X-Forwarded-For` (default `1`, for a single nginx).

To rotate `DJANGO_SECRET_KEY` later without signing everyone out, see [security.md#rotating-secret_key](security.md#rotating-secret_key).

## Capacity

Size the containers from the server's RAM before going live, and set `vm.overcommit_memory = 1` on the host for Valkey. The numbers and the defaults are in [deploy.md#capacity](deploy.md#capacity).

## Slow queries

Tests catch N+1 queries on small data, not a missing index on real data. The production PostgreSQL finds those: it loads `pg_stat_statements` and logs every statement slower than `POSTGRES_LOG_MIN_DURATION_MS` (500 ms; `-1` turns the log off). Size its memory in `docker/prod/.env`: `POSTGRES_SHARED_BUFFERS` about 25% of the RAM, `POSTGRES_EFFECTIVE_CACHE_SIZE` about 50 to 75%, and a small `POSTGRES_WORK_MEM`, which applies to every sort or hash step of every query.

Create the extension once, then list the ten statements that took the most total time:

```bash
docker compose -f docker/prod/compose.yaml exec postgres_db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

```sql
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

SELECT calls,
       round(total_exec_time) AS total_ms,
       round(mean_exec_time::numeric, 1) AS mean_ms,
       left(query, 120) AS query
FROM pg_stat_statements
ORDER BY total_exec_time DESC
LIMIT 10;
```

`SELECT pg_stat_statements_reset();` starts a new measurement. The slow statements appear in `docker compose -f docker/prod/compose.yaml logs postgres_db` (`duration: ... ms  statement: ...`). That log contains the SQL with its parameters, so it can hold personal data: keep it under the same retention as other logs.

## Server clock

Containers use the host clock. TOTP codes (allauth MFA), token and verification-code expiry, and log ordering all depend on it, so keep it synchronized:

```bash
timedatectl status   # must show "System clock synchronized: yes"
sudo timedatectl set-ntp true   # systemd-timesyncd; or install chrony
```

`DJANGO_TIME_ZONE` (default `UTC`) only changes how dates are shown and where a day starts; data is always stored in UTC.

## Backups

Backups are the operator's job; the template ships no tooling. The database is the Docker volume `{PROJECT_NAME}-prod_postgres_db_data`. Copying that volume while PostgreSQL runs can save a broken copy, so dump the database instead and keep the file off the VPS:

```bash
docker compose -f docker/prod/compose.yaml exec -T postgres_db \
  pg_dump -U django project_db > /var/backups/project_db-$(date +\%F).sql
```

`django` and `project_db` are `POSTGRES_USER` and `POSTGRES_DB` from `docker/prod/.env`. The `\%` keeps cron from reading `%` as a line break, and works in a shell too. Put the command in cron and copy the `.sql` somewhere else.

Valkey needs no backup. Its volume (`{PROJECT_NAME}-prod_valkey_data`) only saves a restart from losing the cached sessions and the task queue; sessions are also in PostgreSQL, and everything else in it is rebuilt or starts again. Losing it loses the tasks that were still queued.

## Expired sessions

Every app login (`X-Session-Token`) is a row in `django_session`, and Django never deletes expired rows by itself, so the table and its indexes grow forever: more disk, longer vacuums, bigger dumps. Delete them daily from the host's crontab:

```text
15 4 * * * cd <path-to-checkout> && docker compose -f docker/prod/compose.yaml exec -T django python manage.py clearsessions
```

`<path-to-checkout>` is the folder that holds this repository on the server. `clearsessions` only deletes rows whose `expire_date` has passed, so it is safe to run while the site is up. No scheduler container is needed; Celery beat is an optional compose profile ([tasks.md](tasks.md#periodic-tasks-beat)). The copy of a session in Valkey expires by itself.

## Environment variables

Which file holds each environment's variables and how to change a value. No file is shared between environments:

- Development: the root `.env` holds Django's values; `docker/dev/.env` holds `PROJECT_NAME`, the published ports, `HOST_UID` and `HOST_GID`.
- Production: `docker/prod/.env` holds everything.

To change a value, edit that file and recreate the containers; Compose recreates only those whose values changed:

```bash
docker compose -f docker/dev/compose.yaml up -d
docker compose -f docker/prod/compose.yaml --env-file docker/prod/.env up -d
```

A change to `HOST_UID`, `HOST_GID`, `DOCKER_PROD_USER_UID`, `DOCKER_PROD_USER_GID` or `DOCKER_PROD_UV_EXTRAS` also needs `--build`. Every variable, with its purpose, default and file: [environment/](environment/README.md).

## Per-project options

Left out of the template on purpose. Add one when a project needs it:

- **A second Valkey for the task queue:** one instance holds the cache and the queue, and `volatile-lru` already keeps eviction away from the queue. When cache load must not touch tasks at all, add another Valkey service with `maxmemory-policy noeviction` and point `CELERY_BROKER_URL` at it. That variable takes a full URL with its credentials and replaces the one built from `VALKEY_*`, so it is a secret.
- **Separate Valkey users for the cache and the broker:** with `ACL SETUSER`, one user limited to database 0 and another to database 1 (`db=0`, `db=1`). The cache takes `VALKEY_USERNAME` and `VALKEY_PASSWORD`; the broker takes a full `CELERY_BROKER_URL` with the other user. A leaked cache credential then cannot read or forge tasks.
- **Celery results (`AsyncResult`, chords):** the template stores none; the outcome of a task lives in the row it updates. A project that needs them sets `CELERY_RESULT_BACKEND` to database 2 of Valkey, `CELERY_TASK_IGNORE_RESULT = False` and a `CELERY_RESULT_EXPIRES`, so results expire like every other key.
- **Filtering and ordering:** a Ninja `FilterSchema` per list operation (`filters: Query[ThingFilter]`, then `filters.filter(queryset)`), with an explicit whitelist of fields ([Ninja: filtering](https://django-ninja.dev/guide/filtering/), [combining with pagination](https://django-ninja.dev/guide/filtering/#combining-with-pagination)), and an ordering parameter limited to a `Literal` of allowed fields. Every field offered needs an index (`core/tests/ninja/test_list_indexes.py`), or a client can ask for a sequential scan.
- **Object permissions:** a reusable owner check in the service (an owner field, `__` paths allowed) as a second layer on writes; scoping the selector's queryset to the caller stays the first layer.
- **Soft delete:** an opt-in `SoftDeleteModel` with `deleted_at`, a default manager that hides deleted rows plus an `all_objects` manager, `soft_delete` and `restore` services, and unique constraints with `condition=Q(deleted_at__isnull=True)`.
- **Fixing a mistyped email during the pending verification:** `ACCOUNT_EMAIL_VERIFICATION_SUPPORTS_CHANGE = True` lets the user change the address inside the `verify_email` flow (`POST /_allauth/app/v1/account/email` with the pending session token). Weigh the account-enumeration limit allauth notes for it before turning it on.
