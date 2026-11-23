# AGENTS.md

Django 6.1 on Python 3.14, managed with uv. Pure JSON API with Django Ninja (Pydantic schemas, OpenAPI 3.1 in `docs/openapi.json`); accounts through django-allauth headless. No HTML views, no i18n (`USE_I18N = False`), no `django.contrib.sites`. Valkey is on by default (cache DB 0, Celery broker DB 1). Celery is wired and a worker runs by default; no business task ships.

[docs/README.md](docs/README.md) · [NEXT-STEPS](docs/NEXT-STEPS.md) · [architecture](docs/ARCHITECTURE.md) · [API](docs/api.md) · [data](docs/data.md) · [security](docs/security.md) · [email](docs/email.md) · [files](docs/files.md) · [tasks](docs/tasks.md) · [settings](docs/settings.md) · [development](docs/development.md) · [deploy](docs/deploy.md) · [CI](docs/ci.md)

Mandatory rules and links only. The "why" is in `docs/`, and the reasons for each design decision in [docs/decisions/](docs/decisions/README.md): read the record before undoing what it covers. Recipes are in `.agents/skills/`. Do not copy area-doc material back here.

## Init

- Fresh folder copy: `git init` first.
- If `pyproject.toml` still says `name = "project"`, run [.agents/skills/personalize-django-template](.agents/skills/personalize-django-template/SKILL.md) before anything else.
- At kickoff, fill **Domain invariants** below with the product's rules, before adding domain apps. Do not create a glossary file.

## Language

All shipped repository text is English. Surface list: [.agents/rules/only-english-code.md](.agents/rules/only-english-code.md) (keep in lockstep). Do not add `gettext_lazy`, translation files, or `LocaleMiddleware`.

This rule is proactive. When the user names a domain concept in Spanish or another language, translate it yourself into an English identifier before writing code, without asking for the English form and without a glossary. Follow existing models: short PascalCase types, snake_case fields, no filler such as `Model` or `Data` on the type. Example: "documentos casa" becomes `HouseDocument` (not `DocumentosCasa`, `HouseDocumentsModel`, or `Documentos_casa`). If several translations fit, pick one, use it consistently, and mention the choice when reporting the change. Do not add `docs/glossary.md`.

Do not translate or rename the Spanish-by-design exceptions: the whole `bruno/` collection (names and console messages; URLs, JSON keys, and headers stay English), and [.agents/skills/personalize-django-template/SKILL.md](.agents/skills/personalize-django-template/SKILL.md). The conversation with the user, and planning write-ups, may be in another language.

## Layout

`config/`: settings (`base`, `local`, `test`, `production`), URLs, WSGI, Celery app. `apps/<app>/`: `models.py`, `services.py` (writes), `selectors.py` (reads), `api/`, `tests/`; service and selector rules in [docs/ARCHITECTURE.md#services-and-selectors](docs/ARCHITECTURE.md#services-and-selectors). Set `AppConfig.name = "apps.<app>"` (never a bare `"<app>"`) and register that dotted path in `LOCAL_APPS`. `core/`: shared code. `templates/`: allauth plain-text email only. `docker/dev/` and `docker/prod/`: one stack per environment (`.devcontainer/` reuses dev). `bruno/`: Spanish display names, English URLs. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#layers) · [docs/settings.md](docs/settings.md)

## Skills

- [.agents/skills/personalize-django-template](.agents/skills/personalize-django-template/SKILL.md) — first, when `name = "project"`.
- `.agents/skills/add-model-endpoint` — model, cached selector, invalidation, schemas, Ninja operations, router, error tests, Bruno request.
- `.agents/skills/maintain-django-template` — read-only audit of outdated dependencies, images, hooks, actions, tool pins and end-of-life dates, then a tiered upgrade plan; upgrades only after approval.
- Optional `.agents/skills/graphify`: local use only; see **Graphify**.

## Commands

Run inside the dev container (`docker compose -f docker/dev/compose.yaml exec django ...` from the host, or directly in the Dev Container). A second checkout needs its own `PROJECT_NAME` and `DOCKER_DEV_*_PORT` values. [docs/development.md](docs/development.md#checks) · [dev-stack](docs/development.md#dev-stack) · [worktrees](docs/development/with-docker.md#worktrees) · [CI](docs/ci.md)

```bash
python manage.py seed_dev   # idempotent dev users (DEBUG only); apps add data in apps/<app>/seed.py
scripts/check.sh            # or scripts/check.sh lint / test
python manage.py export_openapi_schema --settings config.settings.test --api config.api.api --indent 2 > docs/openapi.json
```

Tests marked `valkey` need the dev-stack Valkey; `pytest -m "not valkey"` runs the rest.

Typing: pyright and mypy (django-stubs plugin) both pass in `scripts/check.sh lint`. pyright is strict on `apps/*/api/` and `core/api/schemas.py`: no `Unknown` types ([docs/typing.md#strict-api](docs/typing.md#strict-api)). Annotate every model field with its django-stubs generic type. Prefer a real type to a suppression; a suppression is rule-scoped and per checker (`# pyright: ignore[rule]`, `# type: ignore[code]`), and one that stays goes in [docs/typing.md#upstream-workarounds](docs/typing.md#upstream-workarounds).

## API

[docs/api.md](docs/api.md). Staff-only Swagger: `/api/v1/docs/`. Or use Bruno.

API routes live under `/api/v1/`, use kebab-case static segments, and end in `/` (missing slash → 404, no redirect). `/_allauth/` has no trailing slash (a slash is 404). The admin still redirects. Thin views: validate, call one service or selector, return a schema. Expose `public_id` (UUIDv7) as string `id` and look objects up by it; integer pks never leave the server, including `/_allauth/` payloads. Scope list and detail lookups to the requester and test that another user's object is 404. Request schemas use Pydantic with `extra="forbid"` (unknown key → 400 `unknown_field`). Selectors own the query shape.

API errors use `{"code", "detail"}` (plus `fields` for validation); `/_allauth/` keeps allauth's format. Raise `ApplicationError` or `ConflictError` with a registered code; never raise a Ninja or Django HTTP exception for a domain rule. Unauthenticated **resource** requests under `/api/v1/` return 401 (`WWW-Authenticate: X-Session-Token`). `/api/v1/docs/` and `/api/v1/schema/` are staff-only; the docs page is not a JSON resource. Lists paginate; order querysets; index list filters (`core/tests/ninja/test_list_indexes.py`). `/api/` responses are `no-store`; never cache a session-dependent response; `cache_page` is banned (ruff `TID251`). Compatible additions stay on `/api/v1/`; breaking changes open `/api/v2/`. `docs/openapi.json` is the committed contract. A scoped endpoint passes `throttle=[AnonThrottle(), UserThrottle(), ScopedThrottle(scope="<app>.<action>")]` (`core.api.throttling`; `throttle=` replaces the API-level throttles), registers the rate in `NINJA_DEFAULT_THROTTLE_RATES`, and tests that the N+1th call is 429 `throttled`. [paths](docs/api.md#paths) · [representation](docs/api.md#representation) · [errors](docs/api.md#error-codes) · [pagination](docs/api.md#pagination) · [caching](docs/api.md#caching) · [throttling](docs/api.md#throttling) · [deprecation](docs/api.md#deprecating-endpoints-and-fields)

## Data

Domain models inherit `core.models.BaseModel` (`public_id`, `created_at`, `updated_at`). `User` is the exception: `AbstractBaseUser` + `PermissionsMixin` with its fields declared explicitly, plus its own `public_id`; it does not inherit `BaseModel` and has no timestamps. Its manager extends `BaseUserManager`, never Django's `UserManager`, which requires `username` ([ADR 0014](docs/decisions/0014-user-extends-abstractbaseuser-and-permissionsmixin.md)). `QuerySet.update()` bumps `updated_at`; `bulk_update()` does not. FKs default to `PROTECT`; `CASCADE` only for owned children in `CASCADE_ALLOWED` (`core/tests/test_on_delete_policy.py`). Persist records by deactivating them. Users are never deleted through the ORM (`delete()` raises): deactivate, anonymize, or, as an operator, `manage.py hard_delete_user` ([docs/user-deletion/](docs/user-deletion/README.md)). Enforce invariants in the database and with `full_clean()`. Reference data: data migrations; demo data: `apps/<app>/seed.py`. No JSON fixtures. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#layers) · [docs/data.md](docs/data.md) · [encryption](docs/security.md#recipe-encrypting-sensitive-fields)

### Domain invariants

One bullet each: the rule, where it is enforced, and the proving test. Fill product rules at kickoff; agents must not break them.

Template (already enforced):

- Email is unique case-insensitively (`UniqueConstraint(Lower("email"), name="users_user_email_ci_uniq")`, `User.clean` / `normalize_email`; `apps/users/tests/test_managers.py`).
- Users are deactivated or anonymized, never deleted through the ORM (`User.delete()` and the queryset's `delete()` raise; the admin has no delete permission; [docs/user-deletion/](docs/user-deletion/README.md); `apps/users/tests/test_deletion.py`, `apps/users/tests/services/test_user_deactivate.py`, `apps/users/tests/http/test_admin.py`).
- The API identifier is `public_id` (UUIDv7), never the integer pk, including `/_allauth/` (`apps/users/tests/http/test_views.py`, `apps/users/tests/http/test_headless_auth.py`).

Product:

None yet.

## Services

Keyword-only arguments, `transaction.atomic`, `full_clean()` before save. Side effects (email, tasks, cache invalidation, webhooks, files) run in `transaction.on_commit(...)` — `enqueue_on_commit` and `invalidate` already do this. Read-modify-write uses `select_for_update()` inside that transaction.

## Caching (Valkey)

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#caching) · [access](docs/security.md#valkey-access) · [outages](docs/security.md#valkey-outages). Recipe: `.agents/skills/add-model-endpoint`.

Valkey is the only shared-state store. Never `LocMemCache` or `DatabaseCache` outside tests. DBs: 0 cache, 1 broker; the `valkey` suite uses 14 (broker) and 15 (cache) with its own prefix — never `FLUSHDB`. Never put credentials or a DB number in `VALKEY_URL`, and never log a Valkey or broker URL.

Cache hot or expensive reads in selectors with `core.cache.read_through`, never in views. Do not cache `request.user`, auth, permissions, or strictly consistent data. Per-user data includes `user.pk` (or the tenant id) in the key. Namespaces are `"<app>.<resource>"`; never build keys by hand or put PII in a key. Every key has a TTL; never pass `timeout=None`. Cache plain data, not model instances; a namespace that caches instances bumps its version when the model's fields change (`user_get`). Invalidate from the writing service with `core.cache.invalidate` (`on_commit`) and from `post_save`/`post_delete` in `apps/<app>/signals.py`. `update()` / `bulk_*` / `RunPython` send no signals: call `invalidate()` after them.

Sessions are `cached_db`: delete with `SessionStore`, never a `Session` queryset. Reads/writes fail open; deletes raise (except allauth and axes keys); `/health/` is 503 while Valkey is down. Never `cache.clear()` outside `conftest.py` (`scripts/check.sh lint`; it is `FLUSHDB`). Never tie `DJANGO_CACHE_VERSION` to `APP_RELEASE`. Tests: [docs/development.md](docs/development.md#cache-writes).

## Background tasks (Celery)

[docs/tasks.md](docs/tasks.md) · [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#background-tasks). Wired in `config/celery.py` (broker on Valkey DB 1); a worker runs by default; no business task ships.

Add a task only when the work does not fit a request. `@shared_task` in `tasks.py` with `name="<app>.<task>"`. No `django.tasks` (`core/tests/test_task_conventions.py`). Tasks are thin (load by ID, call a service). Pass IDs and plain JSON kwargs, never models, files, or secrets. Enqueue with `core.tasks.enqueue_on_commit`, never `delay()` / `apply_async()` / `delay_on_commit()`. Tasks must be idempotent. Retry only transient errors, never `Exception`. Results are not stored; the API returns 202 and the client polls. Soft time limit < hard < `visibility_timeout` (a test enforces it). Statement timeouts: [docs/tasks.md](docs/tasks.md#database-timeouts-in-tasks). Eager mode and `memory://` exist only in `config/settings/test.py`; production refuses both. Tests: `task.apply(kwargs=...)` and `django_capture_on_commit_callbacks(execute=True)` plus a patched `apply_async` (one call after commit, none after rollback). Periodic tasks: `CELERY_BEAT_SCHEDULE` and the `beat` profile (one replica).

## Testing

[docs/development.md](docs/development.md#pytest) · [fixtures](docs/development.md#test-fixtures) · [N+1](docs/development.md#n-plus-one) · [coverage](docs/development.md#coverage) · [layout](docs/development.md#test-layout)

Layout: `apps/<app>/tests/{services,selectors,http,ninja}/` and `core/tests/{http,ninja}/`, plus model/manager tests when needed. `http/` is the HTTP contract: `django.test.Client` (`client`, `user_client`, `admin_client`), literal paths, no `ninja` import. Never change an assertion under `http/` to make a test pass after a framework change; only the client or the setup may change. `ninja/` tests Django Ninja itself (schemas, routers, exception handlers, paginators, throttles, the schema). A test module never imports another test module: shared helpers go in a support module. `core/tests/test_test_layout.py` enforces both rules; the `http` and `ninja` markers follow the directory (`pytest -m http`). Shared fixtures in root `conftest.py`; one factory-boy factory per model; no JSON fixtures. API tests hit literal `/api/v1/...` paths and assert status, error `code`, and body shape. Every new endpoint has 401, 403 (when it applies), 404 for another user's object, 400 validation, and the happy path; an exact query budget; and, for lists, `assert_constant_queries`. Every service tests its `full_clean()` failure. zeal fails N+1 queries; fix them in the selector and only allowlist third-party models. Warnings are errors. Register custom pytest markers in `pyproject.toml`. Every cached selector tests that a second call makes zero queries, that a write through its service returns fresh data, and that a rolled-back write leaves the cache untouched. Query budgets are measured with the test cache, so a cached selector is always a miss. Paginated querysets must be ordered. `fail_under = 98` is branch coverage: never lower it, and do not ratchet it toward 100.

## Migrations

Name them `--name <verb_noun>`. Never edit a migration that may already have been applied. Every `RunPython` has a `reverse_code` and uses `apps.get_model`. CI unapplies and reapplies local apps (`scripts/local_apps_with_migrations.py`). [docs/ci.md](docs/ci.md#reversible-migrations)

A non-nullable or unique column on a populated table takes three migrations (add nullable, backfill, constrain). A callable default such as `uuid.uuid7` may stamp every existing row with one value. Large indexes: `AddIndexConcurrently` with `atomic = False`. Several django replicas: `DJANGO_MIGRATE_ON_START=false` and `docker/prod/migrate.sh` once per deploy. [docs/deploy.md](docs/deploy.md#migrate)

## Security, email, and files

Read env vars through `env` in `config/settings/base.py`. New production variables also go in `docker/prod/.env.example`. Production refuses example or weak secrets (`config/settings/guards.py`). [docs/settings.md](docs/settings.md) · [docs/security.md](docs/security.md#production-secrets)

Email uses `MAILERS`, not `EMAIL_*`. Dev → Mailpit; tests → locmem. Send email in the request; Celery is a per-project option. [docs/email.md](docs/email.md). `FileField`/`ImageField`: `core.files.UploadTo` and `MaxFileSizeValidator`; never store the client filename; no unsanitized HTML/SVG. [docs/files.md](docs/files.md)

A model with a file follows `.agents/skills/add-model-endpoint` plus [docs/files.md](docs/files.md) (private storage, download operation, multipart `File` parameters only on the upload).

New auth or secret headers go in `core.sentry.SENSITIVE_HEADERS`. Sentry's `RedisIntegration` stays disabled. django-axes protects only the admin login (`core.admin_lockout.is_outside_admin`). [docs/security.md](docs/security.md#error-tracking) · [admin lockout](docs/security.md#admin-lockout)

## Dependencies and commits

Pin dependencies with `==` and commit `uv.lock`. Run `uv lock` and `uv add --no-sync`, then `uv sync --locked` in the container; the dev container runs as your `HOST_UID`, so `uv.lock` stays yours ([running as root](docs/development/with-docker.md#running-as-root)). Apply Django and django-allauth security releases at once. A failing `audit` job is fixed by bumping the pin, never by ignoring it, unless `--ignore-vuln <ID>` is justified in a comment with an expiry date. [docs/ci.md](docs/ci.md#audit)

Commits follow Conventional Commits in English (`feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `chore:`, `ci:`), imperative mood.

## Graphify

Optional local tool; it complements [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and does not replace it. Run with `--code-only` or `GRAPHIFY_BACKEND=none`. No Graphify Cloud, no `--strict`. Output: `graphify-out/` (gitignored). [docs/development.md](docs/development.md#graphify)
