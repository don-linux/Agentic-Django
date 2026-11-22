# Development

Local work on a project built from this template. How to bring the stack up is in [development/](development/README.md); this page holds what both setups share.

## Dev stack

Run the stack in Docker ([development/with-docker.md](development/with-docker.md)) or directly on your machine ([development/without-docker.md](development/without-docker.md)). The steps both setups share are in [development/README.md](development/README.md).

## Personalize

The template uses generic placeholders. Ask your AI agent to run the `personalize-django-template` skill: it asks for the project name, Python slug, Docker name, domain, database and admin, then renames every placeholder.

| Placeholder | What it is |
| --- | --- |
| `project` | Python package name in `pyproject.toml`, the Bruno collection and the prefix of the cache keys |
| `Project` | Display name: the API title and description, and the email From, subject and signature in the env examples (the code defaults stay `Project`) |
| `project-name` | Docker name: `PROJECT_NAME` and the Dev Container name |
| `example.com` | Domain |
| `project_db` | Database name |
| `admin@example.com` | The commented `DJANGO_ADMINS` example in `docker/prod/.env.example`: who gets the error emails |

It also runs `uv lock`, because the lock file stores the project name. Secrets, SMTP credentials and ports stay with you: the skill lists them when it finishes.

Without an agent, follow the same order by hand ([the skill](../.agents/skills/personalize-django-template/SKILL.md) lists every file):

1. `project`: the Python slug in `pyproject.toml` (`name`), `bruno/opencollection.yml` (`info.name`) and the `DJANGO_CACHE_KEY_PREFIX` default in `config/settings/base.py`.
2. `Project`: `SPECTACULAR_SETTINGS` (`TITLE`, `DESCRIPTION`) in `config/settings/base.py`, and the email variables of `.env.example` and `docker/prod/.env.example`. Leave the code defaults in `config/settings/base.py` and `config/settings/test.py` as they are.
3. `project-name`, `project_db` and `example.com`: `PROJECT_NAME` in `docker/dev/.env.example` and `docker/prod/.env.example`, `.devcontainer/devcontainer.json`, the `/srv/project-name` paths, `POSTGRES_DB`, and the hosts and origins in `docker/prod/.env.example` and `docs/NEXT-STEPS.md`.
4. Run `uv lock`, then regenerate `docs/openapi.yaml` ([without-docker.md](development/without-docker.md#checks) has the command).
5. Run the placeholder sweep and the checks from the skill's closing section ([Cierre](../.agents/skills/personalize-django-template/SKILL.md#cierre)).

## Accounts

Users sign up and verify over the headless API:

1. `POST /_allauth/app/v1/auth/signup` with `{"email": "...", "password": "..."}`. The response is a 401 with the `verify_email` flow pending. The session token is in the JSON body, as `meta.session_token` (there is no `X-Session-Token` response header): send it as the `X-Session-Token` header on the next calls.
2. The verification code arrives in Mailpit, in the message body. Send it with `POST /_allauth/app/v1/auth/email/verify` and `{"key": "<code>"}`.
3. `POST /_allauth/app/v1/auth/email/verify/resend` (same header) sends a new code, up to two times per flow; logging in again starts a new flow.

`python manage.py createsuperuser` creates a staff account. Staff sign in at `/admin/` (`/` redirects there only when `DEBUG=True`), and `/api/v1/docs/` requires that session.

## Seed dev

`python manage.py seed_dev` creates demo users with a known password, and running it again changes nothing. Each app can add its own demo data in `apps/<app>/seed.py` (a `seed_dev()` function). Reference data that every environment needs belongs in a data migration instead. The command refuses to run with `DEBUG=False` unless you pass `--allow-non-debug`, which is never for production.

It creates `admin@example.com` (a superuser) and `user@example.com`, both with the password `dev-password-123`.

## Bruno

The `bruno/` collection (request names in Spanish) covers the account flow and the users API: sign up, verify, log in, the profile, list, detail and PATCH by public id, the unauthenticated and not-found answers, a wrong login, resending the code, password reset and its confirmation, and logout. Its `Sistema` folder checks the health probes. Every request asserts its status and body.

It has two environments:

- `seed`: the `seed_dev` user (`user@example.com`, `dev-password-123`). The requests tagged `smoke` run headless with it, locally and in CI.
- `local`: for trying things by hand, including signing up a new account.

```bash
python manage.py seed_dev
cd bruno && npx --yes @usebruno/cli@4.3.0 run --env seed --tags=smoke
```

Logout must find a session token (it fails otherwise, since allauth answers 401 even without one), and the next request proves the old token no longer works. The CLI may print a `toBrunoAuth` warning; it comes from Bruno itself and is harmless.

The environment files hold only fixed values: the URLs, and the email and password to log in with. What a script captures (the session token, the user id, the reset session) goes into runtime variables (`bru.setVar`): Bruno keeps them in memory until it closes, or until the CLI run ends, and never writes them to disk, so a run leaves `bruno/` unchanged. They belong to the collection, not to an environment: after reopening Bruno or switching between `seed` and `local`, run "Iniciar sesión" again. Never call `bru.setEnvVar` in a script: since Bruno 4 it saves the value into the environment file, where a commit would publish it, and `core/tests/test_bruno_scripts.py` fails. CI runs the same smoke requests in the `bruno` job ([ci.md#bruno-smoke](ci.md#bruno-smoke)).

Signup, verification and password reset stay manual, because their codes arrive in Mailpit. "Verificar correo" and "Confirmar restablecimiento" ask for the code when you send them (a prompt variable, never saved), so the CLI and the Collection Runner skip them. With code-based password reset, requesting a reset answers 401 with a pending `password_reset_by_code` flow and keeps its session token; then send "Confirmar restablecimiento" with the code, which answers 401 too (the new password is set, and the user is not logged in).

## Checks

`scripts/check.sh` is the one entry point for local runs and CI. `scripts/check.sh lint` runs ruff, the `cache.clear()` guard, mypy, the import contracts (`lint-imports`, [ARCHITECTURE.md#import-contracts](ARCHITECTURE.md#import-contracts)), django-upgrade and the OpenAPI validation and drift check (`scripts/check.sh openapi` runs only that check); `scripts/check.sh test` checks for missing migrations, then runs pytest with coverage; with no argument it runs both. `scripts/check.sh reversible` unapplies and reapplies each local app's migrations, as CI does ([ci.md#reversible-migrations](ci.md#reversible-migrations)). It is not everything CI runs ([ci.md#workflows](ci.md#workflows)). It uses `config.settings.test`, unless the shell already exports another `DJANGO_SETTINGS_MODULE`, and `docs/openapi.yaml` is generated with those settings. The test part needs PostgreSQL. Without Docker, run it as `uv run --env-file .env scripts/check.sh` ([development/without-docker.md#checks](development/without-docker.md#checks)).

## Pre-commit

Install the Git hooks once after cloning. They run on the host (ruff, django-upgrade, file hygiene, `uv lock`, gitleaks, markdownlint); mypy and pytest stay in the container and CI:

```bash
uv tool install pre-commit==4.7.0
pre-commit install
```

`pre-commit run --all-files` runs every hook on the whole repository, as CI does. The markdownlint rules are in `.markdownlint-cli2.yaml` ([ci.md#docs-lint](ci.md#docs-lint)).

## Pytest

Running the test suite.

The tests marked `valkey` (`core/tests/test_valkey_integration.py` and `core/tests/drf/test_valkey_integration.py`) are the only ones that reach a server: the Valkey of the dev stack at `VALKEY_URL`, or the CI service. Without it they fail, never skip, because a skipped integration suite in CI would hide exactly what it is for; run the rest with `pytest -m "not valkey"`. They use databases 14 (broker) and 15 (cache) with a key prefix of their own, and delete only their own keys, one by one: nothing empties a whole database.

### Coverage

`coverage report` fails below `fail_under = 98` (branch coverage, `pyproject.toml`). The floor stays at 98: never lower it, and do not ratchet it toward 100. New tests target security and data branches, not the percentage.

### N plus one

[django-zeal](https://github.com/taobojlen/django-zeal) fails any test request that runs an N+1 query, and only logs a warning under the local settings. It watches requests through its middleware only, since a global context would also watch factory setup and report false positives. Its threshold is 3, because allauth and factory-boy legitimately repeat a lookup twice in one request. `ZEAL_SHOW_ALL_CALLERS` reports the real call site instead of the first middleware frame, and `account.EmailAddress` is allowlisted because allauth fetches it several times per request. Fix an N+1 with `select_related`/`prefetch_related` in the selector; only third-party models go on the allowlist.

### SQL log

Set `DJANGO_LOG_SQL=True` to print every query under the local settings. Django's `django.db.backends` logger writes at DEBUG level only while `DEBUG` is true.

Query budgets (`django_assert_num_queries`) are measured with the test cache (LocMem) and `cached_db` sessions; in production Valkey serves throttling, rate limits and sessions, so they add no queries. A cached selector is always measured as a miss, because tests never commit.

### Test layout

Tests are grouped by what they depend on, so the API framework can change without touching the contract tests:

- `apps/<app>/tests/http/` and `core/tests/http/`: the HTTP contract. Each test calls a literal path (`/api/v1/...`, `/_allauth/...`, `/admin/...`, `/health/...`) with `django.test.Client` and asserts the status, the headers and the body. These modules never import `rest_framework` or `drf_spectacular`. The root fixtures `client`, `user_client` (logged in with `force_login()`) and `admin_client` authenticate; `throttle_rates` and `trusted_proxies` set the throttle rates and the trusted proxy count. Never change an assertion here to make a test pass after a framework change: only the client or the setup may change.
- `apps/<app>/tests/drf/` and `core/tests/drf/`: tests of DRF and drf-spectacular themselves, such as serializers, viewsets, routers, the exception handler, paginators, throttle classes, parsers and the OpenAPI schema. A move to another framework rewrites or deletes them, together with `core/tests/drf/adapters.py`, which implements `throttle_rates` and `trusted_proxies`.
- `apps/<app>/tests/services/`, `apps/<app>/tests/selectors/` and the rest of `apps/<app>/tests/` and `core/tests/`: framework-neutral tests of models, services, selectors, settings, the cache, tasks and tooling.

A helper that several test modules use lives in a support module (`core/tests/routes.py`, `core/tests/cache_helpers.py`, `core/tests/valkey_fixtures.py`), never in a `test_*.py`: a test module never imports another one. `core/tests/test_test_layout.py` enforces both rules, and also fails when a module outside `tests/drf/` imports from one.

The root `conftest.py` marks every test under `tests/http/` with `http` and every test under `tests/drf/` with `drf`, by path: `pytest -m http` runs the HTTP contract alone and `pytest -m drf` the DRF tests.

## Test settings

How tests choose their settings. The module reference is [settings.md#test](settings.md#test).

## Local settings

How development chooses its settings. The module reference is [settings.md#local](settings.md#local).

## Test fixtures

The shared fixtures live in the root `conftest.py`:

- `CELERY_BROKER_URL` is removed from the environment first: Celery reads it before the Django setting, so an exported value would point the tests at a real broker.
- `clear_caches` empties every cache before each test, and refuses any backend that is not in memory: on Valkey, `clear()` is `FLUSHDB`.
- Uploads go to an in-memory storage, so tests never touch the disk.
- `assert_constant_queries(call, grow)` runs `call()`, adds rows with `grow()`, runs `call()` again and fails when the query count changed: the N+1 guard for list endpoints.

### Cache writes

`core.cache` stores a read and invalidates a namespace only after the transaction commits, and a test never commits, so every read is a miss. Wrap each step in `with cache_writes():` to run those callbacks when the block ends:

```python
with cache_writes():
    thing_get(public_id=...)  # stored when the block ends
with django_assert_num_queries(0):
    thing_get(public_id=...)  # served from the cache
```

### Production settings loader

Tests that need `config.settings.production` import it in a subprocess (`core/tests/production_settings.py`): `production.py` mutates dicts it shares with `base.py` (`REST_FRAMEWORK`, `SPECTACULAR_SETTINGS`), so importing it in the test process would change the settings the run is using. The subprocess starts without any `DJANGO_*`, `POSTGRES_*`, `ALLAUTH_*`, `SENTRY_*`, `VALKEY_*`, `CELERY_*` or `CORS_*` variable, `FRONTEND_URL` or `DATABASE_URL`, so nothing inherited leaks in.

## Packaging

Share the template as a zip built from the last commit:

```bash
scripts/package-template.sh   # writes dist/<repo>-<commit>.zip
```

The script uses `git archive`, so only files tracked at `HEAD` are included: never `.env`, `__pycache__`, caches or uploads from the working tree. It refuses to run with uncommitted changes and checks the zip before keeping it: the contents must match `git ls-files`, and a force-added secret (`.env`, `.envrc`, `id_rsa*`, `id_ed25519*`, `id_ecdsa*`, `id_dsa*`, `secrets.y*ml`, `credentials.json`, `*.pem`, `*.key`), cache or upload refuses the package. Never zip the working directory.

## Graphify

[Graphify](https://github.com/Graphify-Labs/graphify) builds a local code graph that answers "what calls what". It is optional, local and code-only: it never runs in CI, never uses Graphify Cloud, and its output (`graphify-out/`) is ignored by Git. It complements [ARCHITECTURE.md](ARCHITECTURE.md), which keeps the decisions.

Install the CLI once (`uv tool install graphifyy==0.9.109`), then build the graph without any LLM call:

```bash
GRAPHIFY_BACKEND=none graphify extract . --code-only
GRAPHIFY_BACKEND=none graphify cluster-only .
```

The first command writes `graphify-out/graph.json`; the second adds `GRAPH_REPORT.md` and `graph.html`. Do not use `--strict` or `graphify hook install`. The optional agent skill is `.agents/skills/graphify/`.
