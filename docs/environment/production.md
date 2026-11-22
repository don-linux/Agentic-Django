# Production variables

Every variable the production stack reads, with its purpose, whether it is required, and the file it lives in. Precedence and the development files: [README.md](README.md). Rules the settings enforce on these values: [security.md#production-secrets](../security.md#production-secrets).

## `docker/prod/.env`

From `docker/prod/.env.example`, the only production env file. Compose reads it for interpolation (`${VAR}` in `docker/prod/compose.yaml`) and passes it to django, the worker and beat (`env_file`). "Yes" means the stack refuses to start without a real value: an empty value, `change-me`, or an `example.com`/`.org`/`.net` host is refused.

### Compose

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `PROJECT_NAME` | No | `project-name` | Compose project name and the `-prod-django` image name; set it per project. |
| `DOCKER_PROD_DJANGO_BIND` | No | `127.0.0.1` | Address the API port is published on. Keep loopback: Django trusts `X-Forwarded-*` from whoever reaches Gunicorn ([deploy.md#proxy](../deploy.md#proxy)). |
| `DOCKER_PROD_DJANGO_PORT` | No | `8000` | Host port of the API. |
| `DOCKER_PROD_USER_UID` | No | `1000` | UID of the image's `django` user (build time); the uploads folder must belong to it. |
| `DOCKER_PROD_USER_GID` | No | `1000` | GID of the image's `django` user (build time). |
| `DOCKER_PROD_DJANGO_UPLOADS_PATH` | Yes | | Absolute host path of the uploads folder, outside the repo ([deploy.md#uploads](../deploy.md#uploads)). |
| `DOCKER_PROD_DJANGO_GUNICORN_WORKERS` | No | `3` | Gunicorn worker processes ([deploy.md#capacity](../deploy.md#capacity)). |
| `DOCKER_PROD_DJANGO_GUNICORN_THREADS` | No | `3` | Threads per worker. |
| `DOCKER_PROD_DJANGO_GUNICORN_TIMEOUT` | No | `30` | Seconds before Gunicorn restarts a worker that stopped heartbeating; not a request limit. |
| `DOCKER_PROD_DJANGO_GUNICORN_GRACEFUL_TIMEOUT` | No | `25` | Seconds in-flight requests get after SIGTERM; below the 30 s `stop_grace_period`. |
| `DOCKER_PROD_DJANGO_MEM_LIMIT` | No | `1536m` | Memory limit of the django container. |

### Django

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `DJANGO_LOG_FORMAT` | No | `json` | Log lines: `json` (one object per line) or `text`. |
| `APP_RELEASE` | No | `dev` | The deployed commit, in the image, Sentry and the logs; better passed on the build command. |
| `DJANGO_SECRET_KEY` | Yes | | Signing key; 50 random characters. |
| `DJANGO_SECRET_KEY_FALLBACKS` | No | Empty | Previous keys while rotating, comma separated ([security.md](../security.md#production-secrets)). |
| `DJANGO_ADMIN_URL` | Yes | | Path of the admin, hard to guess, with a trailing slash, not under `api/`, `_allauth/`, `static/`, `media/` or `health/`. |
| `DJANGO_ALLOWED_HOSTS` | Yes | | The API host. |
| `DJANGO_TIME_ZONE` | No | `UTC` | Display and date-boundary zone; data is stored in UTC. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | No | Empty | The frontend's origin (`https://host`), not the API host; required for a browser frontend, empty suits token-only or same-origin clients. |
| `DJANGO_MEDIA_URL` | No | `/media/` | URL prefix of public uploads. |

### Security

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `DJANGO_SECURE_SSL_REDIRECT` | No | `True` | Redirect HTTP to HTTPS; the proxy must send `X-Forwarded-Proto: https`. |
| `DJANGO_SECURE_HSTS_SECONDS` | No | `60` | HSTS lifetime; raise it once HTTPS works everywhere ([security.md#hsts](../security.md#hsts)). |
| `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS` | No | `False` | HSTS for every subdomain; needs a year of HSTS. |
| `DJANGO_SECURE_HSTS_PRELOAD` | No | `False` | Ask browsers to preload HSTS; needs a year and subdomains. |
| `DJANGO_SECURE_CONTENT_TYPE_NOSNIFF` | No | `True` | Send `X-Content-Type-Options: nosniff`. |
| `AXES_FAILURE_LIMIT` | No | `5` | Failed admin logins before the lockout ([security.md#admin-lockout](../security.md#admin-lockout)). |

### PostgreSQL

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `POSTGRES_DB` | Yes | | Database name; Compose stops when it is unset or empty. |
| `POSTGRES_USER` | Yes | | Database role. |
| `POSTGRES_PASSWORD` | Yes | | The role's password; never empty. |
| `DATABASE_URL` | No | | A full database URL instead of the `POSTGRES_*` values, for a database outside this compose file. |
| `CONN_MAX_AGE` | No | `60` | Seconds a connection is reused; `0` opens one per request. |
| `CONN_HEALTH_CHECKS` | No | `True` | Check a reused connection first, so a Postgres restart does not fail requests. |
| `POSTGRES_CONNECT_TIMEOUT` | No | `5` | Seconds to wait for a connection (`/health/` then answers 503). |
| `POSTGRES_STATEMENT_TIMEOUT_MS` | No | `30000` | Longest query of a request, in milliseconds; `0` is no limit. |
| `POSTGRES_LOCK_TIMEOUT_MS` | No | `0` | Longest wait for a lock, in milliseconds; `0` is no limit. |
| `DJANGO_MIGRATE_LOCK_TIMEOUT_MS` | No | `10000` | Lock timeout of `migrate` on start, which runs without a statement timeout. |
| `POSTGRES_LOG_MIN_DURATION_MS` | No | `500` | Statements slower than this go to the Postgres log; `-1` turns it off. |
| `POSTGRES_SHARED_BUFFERS` | No | `128MB` | About 25% of the server's RAM. |
| `POSTGRES_EFFECTIVE_CACHE_SIZE` | No | `512MB` | About 50 to 75% of the server's RAM. |
| `POSTGRES_WORK_MEM` | No | `4MB` | Memory per sort or hash step of each query. |
| `DJANGO_MIGRATE_ON_START` | No | `true` | Apply migrations when django starts; `false` with more than one replica ([deploy.md#migrate](../deploy.md#migrate)). |

### Valkey

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `VALKEY_PASSWORD` | Yes | | Password of the Valkey service, 32 characters or more; never inside `VALKEY_URL`. |
| `VALKEY_URL` | No | `redis://valkey:6379` | Only for a managed Valkey: its URL without credentials or a database number; `rediss://` must end with `?ssl_cert_reqs=required`. |
| `VALKEY_MAXMEMORY` | No | `256mb` | Memory Valkey uses before it evicts cache entries. |
| `DOCKER_PROD_VALKEY_MEM_LIMIT` | No | `512m` | Memory limit of the Valkey container, about twice `VALKEY_MAXMEMORY`. |
| `VALKEY_CONNECT_TIMEOUT` | No | `1` | Seconds to wait for a connection. |
| `VALKEY_SOCKET_TIMEOUT` | No | `1` | Seconds to wait for each reply. |
| `DJANGO_CACHE_TIMEOUT` | No | `300` | Default lifetime of a cache entry, in seconds. |
| `VALKEY_MAX_TTL` | No | `2592000` | Lifetime given to a key written without one, in seconds. |
| `DJANGO_CACHE_KEY_PREFIX` | No | `project` | Prefix of every cache key; empty falls back to the default. |
| `DJANGO_CACHE_VERSION` | No | `1` | Raise it to drop every cached entry; never tie it to `APP_RELEASE`. |
| `VALKEY_CACHE_DB` | No | `0` | Logical database of the cache. |
| `VALKEY_BROKER_DB` | No | `1` | Logical database of the task broker; must differ from the cache's. |
| `VALKEY_USERNAME` | No | Empty | ACL user, only for a server that defines one. It needs write commands: the readiness probe is an `EXPIRE` ([ARCHITECTURE](../ARCHITECTURE.md#deployment)). |

### Background tasks

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `CELERY_TASK_SOFT_TIME_LIMIT` | No | `1500` | Seconds before a task gets `SoftTimeLimitExceeded`. |
| `CELERY_TASK_TIME_LIMIT` | No | `1800` | Seconds before a task's process is killed; below 3600. |
| `CELERY_WORKER_CONCURRENCY` | No | `2` | Worker processes. |
| `CELERY_WORKER_MAX_TASKS_PER_CHILD` | No | `100` | Tasks before a worker process is replaced. |
| `CELERY_WORKER_MAX_MEMORY_PER_CHILD` | No | `200000` | KiB a worker process may hold before it is replaced. |
| `CELERY_BROKER_URL` | No | Built from the Valkey values | Only when the broker is not this Valkey; a secret, since it carries credentials. |
| `DOCKER_PROD_CELERY_WORKER_MEM_LIMIT` | No | `768m` | Memory limit of the worker container. |
| `DOCKER_PROD_CELERY_WORKER_STOP_GRACE_PERIOD` | No | `60s` | Time the worker gets on `stop` before SIGKILL. |
| `CELERY_WORKER_LOG_LEVEL` | No | `INFO` | The worker's log level. |
| `CELERY_POSTGRES_STATEMENT_TIMEOUT_MS` | No | `300000` | Statement timeout of the worker's connections, in milliseconds; never `0`. |

### Email

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `DJANGO_DEFAULT_FROM_EMAIL` | Yes | | Sender of every email, on a domain your provider verified ([email.md](../email.md)). |
| `DJANGO_EMAIL_SIGNATURE_NAME` | No | `Project` | Name in the account-email signature. |
| `DJANGO_SERVER_EMAIL` | No | `DJANGO_DEFAULT_FROM_EMAIL` | Sender of error emails. |
| `DJANGO_ADMINS` | No | Empty | Who gets error emails, comma separated; skipped while `SENTRY_DSN` is set. |
| `DJANGO_EMAIL_SUBJECT_PREFIX` | No | `[Project]` and a space | Prefix of every email subject; quote it, or Compose drops the space. |
| `DJANGO_MAILER_TIMEOUT` | No | `10` | Seconds per SMTP operation. |
| `DJANGO_MAILER_HOST` | Yes | | Your provider's SMTP host. |
| `DJANGO_MAILER_PORT` | No | `587` in the example | SMTP port. |
| `DJANGO_MAILER_USERNAME` | No | Empty | SMTP user. |
| `DJANGO_MAILER_PASSWORD` | No | Empty | SMTP password. |
| `DJANGO_MAILER_USE_TLS` | No | `True` | STARTTLS, for port 587. |
| `DJANGO_MAILER_USE_SSL` | No | `False` | Implicit TLS, for port 465. |

### Passwords

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `DJANGO_ARGON2_TIME_COST` | No | `2` | Argon2 passes per hash. |
| `DJANGO_ARGON2_MEMORY_COST_KIB` | No | `102400` | Memory per hash, in KiB; lower it on small servers ([deploy.md#capacity](../deploy.md#capacity)). |
| `DJANGO_ARGON2_PARALLELISM` | No | `8` | Argon2 lanes per hash. |

### Proxies and throttling

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `DJANGO_TRUSTED_PROXY_COUNT` | No | `1` | Reverse proxies that append to `X-Forwarded-For`; sets the client IP for rate limits and throttles. |
| `ALLAUTH_TRUSTED_PROXY_COUNT` | No | `1` | Older name of `DJANGO_TRUSTED_PROXY_COUNT`, read only when that one is unset. |
| `DJANGO_API_MAX_PAGE_NUMBER` | No | `1000` | Deepest page a page-number list serves; deeper is a 404. |
| `DJANGO_THROTTLE_ANON_RATE` | No | `60/min` | API limit per anonymous client IP, like `60/min` or `1000/hour`; another format refuses to start. |
| `DJANGO_THROTTLE_USER_RATE` | No | `600/min` | API limit per authenticated user, like `60/min` or `1000/hour`; another format refuses to start. |

### Accounts and frontend

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `DJANGO_ACCOUNT_ALLOW_REGISTRATION` | No | `True` | Open sign-up. |
| `FRONTEND_URL` | Yes | | Base URL of the links in account emails. |
| `CORS_ALLOWED_ORIGINS` | No | Empty (no cross-origin caller) | The SPA's origin (`https://host`, no path); required for a browser frontend. Example domains and `localhost` are refused ([security.md](../security.md#production-secrets)). |

### Object storage

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `DOCKER_PROD_UV_EXTRAS` | No | Empty | Extra dependencies for the image build; `s3` for object storage. |
| `DJANGO_STORAGE_BACKEND` | No | `local` | `s3` keeps uploads in a bucket instead of the uploads folder ([files.md](../files.md)). |
| `DJANGO_AWS_STORAGE_BUCKET_NAME` | With `s3` | | The bucket. |
| `DJANGO_AWS_S3_REGION_NAME` | No | | The bucket's region. |
| `DJANGO_AWS_S3_ENDPOINT_URL` | No | | Endpoint of a non-AWS provider. |
| `DJANGO_AWS_S3_CUSTOM_DOMAIN` | No | | CDN or custom domain for public files. |
| `DJANGO_AWS_QUERYSTRING_EXPIRE` | No | `300` | Seconds a signed URL for a private file stays valid. |
| `AWS_ACCESS_KEY_ID` | With `s3` | | Read by boto3, never by the settings. |
| `AWS_SECRET_ACCESS_KEY` | With `s3` | | Read by boto3, never by the settings. |

### Sentry

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `SENTRY_DSN` | No | Empty (Sentry off) | The project's DSN ([security.md#error-tracking](../security.md#error-tracking)). |
| `SENTRY_ENVIRONMENT` | No | `production` | Environment name in Sentry. |
| `SENTRY_TRACES_SAMPLE_RATE` | No | `0.0` | Share of requests traced. |

## Set by the image or Compose

Never put these in `docker/prod/.env`: the image or the compose file sets them, and Compose's `environment:` wins over the env file.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `DJANGO_SETTINGS_MODULE` | | `config.settings.production` | Pinned by Compose for django, the worker and beat; `start.sh` and `start-worker.sh` refuse any other module. |
| `POSTGRES_HOST` | | `postgres_db` | The compose service. |
| `POSTGRES_PORT` | | `5432` | The compose service's port. |
| `DJANGO_MEDIA_ROOT` | | `/app/uploads/public` | Public uploads, in the image. |
| `DJANGO_PRIVATE_MEDIA_ROOT` | | `/app/uploads/private` | Private uploads, in the image. |
| `DJANGO_PRIVATE_MEDIA_ACCEL_REDIRECT_PREFIX` | | `/protected-media/` | The nginx `internal` location for private files, in the image. |
| `REMAP_SIGTERM` | | `SIGQUIT` | Worker only: `docker stop` returns unfinished tasks to the queue. |
