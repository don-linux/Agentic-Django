---
name: add-model-endpoint
description: Add a domain model and its /api/v1 endpoint using this template's layout — BaseModel with UUIDv7 public_id and timestamps, keyword-only services, selectors (cached when the read is hot), BaseModelSerializer, viewset, throttle scope, OpenAPI, tests including negative paths, a Bruno request, and a named migration. Use when the user asks to add a model, a resource, a CRUD or list/detail endpoint, or a new API under /api/v1.
---

# Add a model and its API endpoint

Follow this checklist in order. Mandatory rules stay in [AGENTS.md](../../../AGENTS.md); this skill is the recipe. The "why" and the numbers (TTLs, page sizes, rates) live in the linked docs — do not copy them back into AGENTS.md or into this file.

Worked example in this template (read it; do not clone User blindly): `apps/users/` — `models.py`, `services.py`, `selectors.py`, `signals.py`, `api/serializers.py`, `api/views.py`, `tests/`, and `config/api_router.py`.

`User` is a special case: it subclasses `AbstractUser` and declares its own `public_id`. **New domain models inherit `core.models.BaseModel`.** They do not copy User's inheritance.

## When to run

Run when the user wants a new persisted resource exposed under `/api/v1/`.

If `pyproject.toml` still has `name = "project"` **and** the user is starting a product from this template, stop and run [personalize-django-template](../personalize-django-template/SKILL.md) first. If they explicitly asked to add a model on this still-generic tree (for example while editing the template itself), continue.

If the work is only a background job, a file upload, or an opt-in recipe (append-only table, audit log, field encryption), follow the area doc instead of this whole checklist, then return here for any HTTP surface.

## Before writing code

1. Translate any Spanish (or other non-English) domain name yourself into an English identifier, consistent with existing models. Example: "documentos casa" → `HouseDocument`, route `/api/v1/house-documents/`, table/app style `house_documents` / a short English plural app such as `documents` — not `DocumentosCasa`. There is no glossary; do not ask the user to pre-register the term, and do not create `docs/glossary.md`. Rule: [AGENTS.md](../../../AGENTS.md) Language and [.agents/rules/only-english-code.md](../../rules/only-english-code.md).
2. Pick the app (`apps/<app>/`) or create one. Layout: `models.py`, `services.py` (writes), `selectors.py` (reads), `api/serializers.py`, `api/views.py`, `tests/` (`tests/services/`, `tests/selectors/`, `tests/http/` for the HTTP contract, `tests/drf/` only for tests that need DRF itself, plus `test_models.py` when the model has real behaviour), and `tests/factories.py` (one factory-boy factory per model; no JSON fixtures). `AppConfig.name` must be `"apps.<app>"` (never bare `"<app>"`), and `"apps.<app>"` must be listed in `LOCAL_APPS` (`config/settings/base.py`). Import signals from `AppConfig.ready()` when the app has receivers. Shared helpers stay in `core/`; do not put domain code there.
3. Read [docs/ARCHITECTURE.md](../../../docs/ARCHITECTURE.md#layers), [performance](../../../docs/ARCHITECTURE.md#performance), [indexes](../../../docs/ARCHITECTURE.md#indexes), [caching](../../../docs/ARCHITECTURE.md#caching), and [docs/api.md](../../../docs/api.md) (`#paths`, `#representation`, `#throttling`, `#error-codes`, `#pagination`).
4. Show a short plan (English names, model fields, routes, who may see a row, whether the read is cached, whether a write starts a task) and wait for confirmation if the user asked for a plan first. Do not break the Domain invariants listed in `AGENTS.md`.

## 1. Model

In `apps/<app>/models.py`:

- Subclass `core.models.BaseModel`. That gives `public_id` (UUIDv7, unique, non-editable), `created_at` and `updated_at` from the database clock. Custom managers subclass `core.models.BaseManager`.
- Put invariants in database constraints where possible (`CheckConstraint`, `UniqueConstraint(..., condition=Q(...))` for partial uniqueness). Services will call `full_clean()` as well.
- Foreign keys default to `on_delete=models.PROTECT`. Use `CASCADE` only for a child owned by its parent, and add the field to `CASCADE_ALLOWED` in `core/tests/test_on_delete_policy.py` with a reason. Use `SET_NULL` only when losing the link is acceptable. Why: [ARCHITECTURE.md#layers](../../../docs/ARCHITECTURE.md#layers).
- Records that must persist are deactivated, not deleted, unless the on-delete policy already covers a true child row.
- File and image fields use `core.files.UploadTo` and `MaxFileSizeValidator`, store a generated name (never the client filename), and must not accept unsanitized HTML/SVG. Why: [files.md#field](../../../docs/files.md#field).
- Sensitive values follow the encryption recipe in [security.md#recipe-encrypting-sensitive-fields](../../../docs/security.md#recipe-encrypting-sensitive-fields) only when the project opts in.
- No `gettext_lazy`. End-user strings are plain English (`USE_I18N = False`).
- Default list ordering that is not the primary key, every `ordering_fields` entry, and every list filter must be indexed. Name indexes `<app>_<model>_<cols>_idx`. `BaseModel.created_at` is **not** indexed: `order_by("id")` is safe; `order_by("-created_at")` needs a named index. Why: [ARCHITECTURE.md#indexes](../../../docs/ARCHITECTURE.md#indexes).

Do not expose the integer primary key anywhere in the API.

## 2. Migration

```bash
python manage.py makemigrations <app> --name <verb_noun>
```

Example name: `create_house_document`. Never edit a migration that may already have been applied; add a new one. Every `RunPython` has a `reverse_code` (`migrations.RunPython.noop` when reversing is meaningless) and uses `apps.get_model`, never a direct model import. A non-nullable or unique column on a populated table takes three migrations: add it nullable (and not unique), backfill in batches, then set `null=False` (and the unique constraint). A callable default such as `uuid.uuid7` may stamp every existing row with one value; do not rely on it to backfill unique ids. Indexes on large tables use `AddIndexConcurrently` in a migration with `atomic = False`. Why: [ARCHITECTURE.md#indexes](../../../docs/ARCHITECTURE.md#indexes), [ci.md#reversible-migrations](../../../docs/ci.md#reversible-migrations). CI proves reversibility with `scripts/local_apps_with_migrations.py`. Reversing an app also unapplies dependent apps (for example `migrate users zero` unapplies a later app that FKs to `User`).

`core/tests/test_migrations_script.py` asserts the script's output equals `local_apps_with_migrations()`. Do not hardcode a new app label in that test.

## 3. Services (writes)

In `apps/<app>/services.py` (naming and signature rules: [ARCHITECTURE.md#services-and-selectors](../../../docs/ARCHITECTURE.md#services-and-selectors)):

- Import the model at module top. Use `TYPE_CHECKING` only for types that would otherwise be unused at runtime (typically `User`). Do not hide the model import inside the function (ruff `PLC0415`).
- One function per write, keyword-only arguments, wrapped in `transaction.atomic`, `full_clean()` before `save()`. `BaseModel.save()` already includes `"updated_at"` when `update_fields` is non-empty.
- Business logic lives here. Views stay thin: validate, call one service or selector, serialize.
- Side effects (cache invalidation, Celery, email, webhooks, files) run in `transaction.on_commit(...)`. Use `core.cache.invalidate("<app>.<resource>")` and `core.tasks.enqueue_on_commit(task, **kwargs)` — they already defer to commit. Never call `task.delay()`, `apply_async()`, or `delay_on_commit()`.
- Read-modify-write on the same row uses `select_for_update()` inside the service transaction (counters, balances, single-use codes).
- Business rejections raise `core.exceptions.ApplicationError` (or `ConflictError` for 409) with a stable `snake_case` code. Register a new code in [api.md#error-codes](../../../docs/api.md#error-codes) when it is first used. Never subclass DRF's `APIException` for domain errors. Do not catch `ValidationError` in the view; `api_exception_handler` already maps it.
- `objects.filter(...).update(...)` already bumps `updated_at`. `bulk_update()` does not: include `updated_at` in its fields. After `queryset.update()`, `bulk_create()`, `bulk_update()`, or `RunPython`, call `invalidate()` yourself (no signals fire). If the selector is cached, also add `post_save` / `post_delete` receivers in `apps/<app>/signals.py` for admin and other non-service writes.

## 4. Selectors (reads)

In `apps/<app>/selectors.py`:

- Every read that is more than a plain lookup lives here. The selector owns the query shape: `select_related()` / `prefetch_related()` (or `Prefetch(...)`) for everything the serializer reads. Views call the selector from `get_queryset()`. Counts and sums come from `annotate()` in the selector.
- Order list querysets deterministically, on an indexed column (`core/tests/drf/test_list_indexes.py` enforces this). Why: [ARCHITECTURE.md#performance](../../../docs/ARCHITECTURE.md#performance), [#indexes](../../../docs/ARCHITECTURE.md#indexes).
- Look objects up by `public_id`, never by integer pk.
- Scope the queryset to the requesting user or tenant.
- Do not query `request.user` again; authentication already loaded it.

### When the read is hot or expensive

Wrap the query in `core.cache.read_through("<app>.<resource>", *parts, ttl=..., loader=...)`. Cache in the selector, never in the view, so authentication, permissions and throttles always run first.

- Every key has a positive `ttl` in seconds (`read_through` has no default and rejects `ttl <= 0`). Pick the value from [ARCHITECTURE.md#caching](../../../docs/ARCHITECTURE.md#caching). Never call the Django cache API with `timeout=None`.
- Data that depends on the user or tenant includes `user.pk` (or the tenant id) in the key parts. Never cache per-user data under a shared key. Never put raw client input, emails, tokens or other PII in a key.
- Prefer plain data (dicts, lists, primitives, serializer output) over model instances. If a namespace caches instances, bump a namespace version when the model changes and defer secret fields. Worked example: `user_get` in `apps/users/selectors.py` (`USER_CACHE_VERSION`, `.defer("password")`).
- Do not cache `request.user`, authentication, permission checks, or data that must be strictly consistent (balances, stock, single-use codes).
- If you cache, also add `apps/<app>/signals.py` with `post_save` / `post_delete` receivers that call `invalidate("<app>.<resource>")`. Import the module from `AppConfig.ready()`.

Why: [ARCHITECTURE.md#caching](../../../docs/ARCHITECTURE.md#caching). Outages: [security.md#valkey-outages](../../../docs/security.md#valkey-outages).

## 5. Serializer

In `apps/<app>/api/serializers.py`:

- Subclass `core.api.serializers.BaseModelSerializer`. An unknown body key is a 400 with code `unknown_field`, not a silent ignore.
- Expose `public_id` as `id`, a string, read-only: `id = serializers.UUIDField(source="public_id", read_only=True)`.
- Hand-written datetime fields use `UTCDateTimeField`. `TIME_ZONE` must not change the JSON (`Z` suffix, UTC).
- Hyperlinked `url` fields use `lookup_field="public_id"`, `lookup_url_kwarg="id"` and the `api:` view name (see `UserSerializer`).
- Why: [api.md#representation](../../../docs/api.md#representation).

## 6. View and URL

In `apps/<app>/api/views.py`:

- Thin viewset: mixins + `GenericViewSet` (or `APIView` when a router is the wrong fit). Keep the view to validate, call one service or selector, and serialize. Do not wrap those calls in `try/except ValidationError`.
- Put `core.api.mixins.PublicIdLookupMixin` first in the bases: it looks rows up by `public_id`, publishes the path parameter as `{id}`, and accepts only a UUID (either case), so an integer pk is a plain 404 (see `UserViewSet`).
- `get_queryset()` calls the selector and is scoped to the requesting user or tenant (unauthenticated → empty queryset). Never trust the class-level `queryset` for authorization (the router may still need it for the basename).
- After an `is_authenticated` (and `pk is None`) guard, pass `request.user` into a service or selector typed as `User` — that guard narrows the type. Do not add a `cast("User", ...)` after the same guard (`redundant-cast`). In `perform_create` / other writes, repeat the guard; without it mypy reports `User | AnonymousUser`. Worked example: `apps/users/api/views.py`.
- Set `http_method_names` explicitly when PUT should stay closed (see `UserViewSet`).
- Register the viewset on the existing `SimpleRouter` in `config/api_router.py`. Every DRF route lives under `/api/v1/` (`config/urls.py` already includes that prefix). Do not add a second router, an API root, or a route under another prefix.
- Static path segments are kebab-case and end with `/`. Extra actions set `@action(..., url_path="change-password")`, never the default `change_password`. At most one level of nesting. A path without the trailing slash must 404 (not redirect). Enforced by `core/tests/http/test_rest_conventions.py`. Why: [api.md#paths](../../../docs/api.md#paths).
- Compatible additions stay on `/api/v1/`. Open `/api/v2/` only for an incompatible change. Deprecate before removing ([api.md#deprecating-endpoints-and-fields](../../../docs/api.md#deprecating-endpoints-and-fields)).
- Create endpoints that clients may retry (mobile, lost response) follow [api.md#idempotent-retries](../../../docs/api.md#idempotent-retries). A first internal POST does not have to implement `Idempotency-Key`; say so in the plan if you skip it.
- Responses are `no-store` by default (`ApiNoStoreMiddleware`). Never `cache_page` or the per-site cache middleware. Why: [api.md#caching](../../../docs/api.md#caching). Pagination defaults are in [api.md#pagination](../../../docs/api.md#pagination).

## Model with a file

When the new model has a `FileField` or `ImageField`, also follow [docs/files.md](../../../docs/files.md) — do not stop at the JSON checklist.

- User-sensitive files use `storage=private_storage`, never serialize `.url`, and are downloaded through a kebab-case action (`@action(detail=True, url_path="download")`) that checks the owner-scoped queryset and returns `protected_file_response(...)`.
- The create/upload action is the only one that opts in to `parser_classes` / `get_parsers()` with `MultiPartParser`. Every other action must still answer a multipart body with 415.
- DRF's `ViewSetMixin.initialize_request` sets `self.action` *after* `get_parsers()` runs. If you switch parsers on `self.action == "create"`, set `self.action` first and then call `APIView.initialize_request`:

```python
def initialize_request(self, request, *args, **kwargs):
    method = request.method.lower() if request.method else "get"
    if method == "options":
        self.action = "metadata"
    else:
        action = self.action_map.get(method)
        if action is not None:
            self.action = action
    return APIView.initialize_request(self, request, *args, **kwargs)
```

- Services that replace or delete a file call `core.files.delete_file_on_commit` before saving or deleting the row.
- If the story does not name allowed extensions or a size cap, pick a tight allow-list (never unsanitized HTML/SVG) and a cap at or below the proxy `client_max_body_size`, and state the choice. Do not invent a `TextChoices` enum for an unspecified "tipo".
- After regenerating `docs/openapi.yaml`, `apps/users/tests/drf/test_openapi.py` must allow `multipart/form-data` only on those upload operations (not `== {"application/json"}` for the whole schema).

## 7. Throttle scope

Global anon/user throttles are already on. For a sensitive or expensive action:

- Set `throttle_scope = "<app>.<action>"` on the view (for example `"orders.checkout"`).
- Register the rate in `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]` in `config/settings/base.py`. An unregistered scope raises `ImproperlyConfigured` on the first request (`core/tests/drf/test_throttling.py`).
- Do not set `throttle_classes` unless the list still includes the global classes; assigning it replaces the defaults.
- Why: [api.md#throttling](../../../docs/api.md#throttling).

## 8. OpenAPI

Annotate views and serializers so drf-spectacular can describe the new paths, error bodies and fields. Follow [api.md#error-responses-in-openapi](../../../docs/api.md#error-responses-in-openapi). Swagger lives at `/api/v1/docs/` (staff). Default 401/403/404/400/429 responses come from `core.api.schema.AutoSchema`. Document any extra error responses as existing endpoints do (`apps/users/tests/drf/test_openapi.py`).

After any API change, regenerate the committed contract with test settings (never hand-edit `docs/openapi.yaml`).

From the host, using the dev compose service (the redirect writes `docs/openapi.yaml` as your host user, whatever `HOST_UID` the container runs with):

```bash
docker compose -f docker/dev/compose.yaml exec -T django python manage.py spectacular --settings config.settings.test --validate --fail-on-warn > docs/openapi.yaml
```

If you are already inside the container, or on a host with the toolchain (`uv run`, as CI does):

```bash
python manage.py spectacular --settings config.settings.test --file docs/openapi.yaml --validate --fail-on-warn
```

`scripts/check.sh lint` and `core/tests/drf/test_openapi_contract.py` fail when the file drifts. A diff that removes or renames a path, field, or enum value is a breaking change and needs `/api/v2/`.

## 9. Tests (including negative paths)

Put them under `apps/<app>/tests/` ([test layout](../../../docs/development.md#test-layout)). API tests go in `apps/<app>/tests/http/`: they hit literal `/api/v1/...` paths with `django.test.Client` (`client`, `user_client`), assert the status, the error `code`, and the body shape, and never import `rest_framework` or `drf_spectacular`. Only a test of a serializer, a viewset hook or the schema goes in `apps/<app>/tests/drf/`. Use the root `conftest.py` fixtures and the model factory. Imports used only as annotations go under `TYPE_CHECKING` (ruff `TC001` / `TC002`).

Every new endpoint needs:

- Happy path (create/list/retrieve/update as exposed).
- `401` unauthenticated (`WWW-Authenticate: X-Session-Token`).
- `403` only when a permission other than "must be logged in" applies. Another user's object is `404`, not `403`, when the queryset is scoped.
- `404` for another user's object, for an unknown `public_id`, and for an integer pk if the detail route uses `public_id`.
- `400` validation: unknown field, `full_clean()` failure surfaced as JSON, and any domain `ApplicationError`.
- An exact query budget (`django_assert_num_queries`). Measure it once with a **session-authenticated** client (LocMem cache + `cached_db` sessions, as in `apps/users/tests/http/test_query_budget.py`), pin the number, and comment what each query is — `full_clean()` often adds existence checks, and a `CheckConstraint` can add a savepoint plus a check query. If the assertion does not print the SQL, dump it with `CaptureQueriesContext`. Do not use DRF's `force_authenticate` for the budget.
- Every list endpoint also gets `assert_constant_queries` that grows related data.
- A service test for the `full_clean()` failure.

If the selector is cached, also test: a second read makes zero queries (`cache_writes` + `django_assert_num_queries(0)`), a write through the service returns fresh data, and a rolled-back write leaves the cache untouched. Behaviour that only a real Valkey can show belongs in `core/tests/test_valkey_integration.py` (marker `valkey`).

If the view has `throttle_scope`, test that the N+1th call returns `429` `throttled`. Set the rate with the `throttle_rates` fixture (`core/tests/drf/adapters.py`); a test under `tests/http/` never patches DRF itself.

Fix N+1 queries in the selector (`select_related` / `prefetch_related`); do not widen `ZEAL_ALLOWLIST` except for third-party models (`config/settings/test.py`). Paginated querysets must be ordered (`UnorderedObjectListWarning` is an error). Do not add `pytest` markers without registering them in `pyproject.toml`. Do not change coverage `fail_under`: it stays at 98.

## 10. Bruno request

Add a request next to the existing collection. Names, folder names, `info.name`, and console messages stay in **Spanish** (do not rename anything already in `bruno/`). URLs, JSON keys, and headers stay the API's English identifiers.

Pattern: `bruno/Cuentas/perfil.yml` and `actualizar-perfil.yml` (`{{baseUrl}}/api/v1/.../`, trailing slash, `auth: inherit`, assertions on status and the shape of `id` as a string). Put a new resource in its own Spanish folder with a `folder.yml`. Tag a smoke request with `tags: [smoke]` only when it is a basic happy path that is safe to run after `seed_dev`. If the endpoint is not for accounts, do not drop it in `Cuentas/`. Also add a denied or unauthenticated request when that is meaningful (`perfil-sin-sesion.yml` is the pattern). A script that captures a value (an id, a token) stores it with `bru.setVar`, never `bru.setEnvVar`: Bruno 4 saves that into `bruno/environments/*.yml`, and `core/tests/test_bruno_scripts.py` fails. A value a person types, such as a code from Mailpit, is a prompt variable (`{{?Código de verificación de Mailpit}}`). How the collection is organised: [development.md#bruno](../../../docs/development.md#bruno).

For an upload, add a request in the new Spanish folder with a multipart body.
URLs and field names stay English. This YAML shape matches the existing
collection's `http.body` style:

```yaml
http:
  method: POST
  url: "{{baseUrl}}/api/v1/house-documents/"
  auth: inherit
  body:
    type: multipartForm
    data:
      - name: name
        type: text
        value: Escritura
      - name: document_type
        type: text
        value: deed
      - name: file
        type: file
        value: "@file(escritura.pdf)"
```

Tag `smoke` only for a basic authenticated list (or an unauthenticated 401) that
is safe after `seed_dev`. Do not tag the upload if it needs a fixture file the
smoke job does not ship.

## 11. Optional extras

- **Admin:** if the model is edited in Django admin, use `list_select_related` for FKs in `list_display`, `show_full_result_count = False` on large tables, and `raw_id_fields` or `autocomplete_fields` instead of a `<select>` that loads every row. See [ARCHITECTURE.md#performance](../../../docs/ARCHITECTURE.md#performance).
- **Seed data:** demo rows go in `seed_dev()` inside `apps/<app>/seed.py` (DEBUG only, idempotent). Reference data that every environment needs goes in a data migration, not in the seed.
- **Domain invariants:** if the new model introduces a rule the data must always satisfy, add a bullet to the Domain invariants section of AGENTS.md (where it is enforced, and which test proves it).

## 12. Close-out

From the host (dev container or the running compose service):

```bash
docker compose -f docker/dev/compose.yaml exec -T django python manage.py spectacular --settings config.settings.test --validate --fail-on-warn > docs/openapi.yaml
docker compose -f docker/dev/compose.yaml exec django scripts/check.sh
```

Inside the container. On the host, prefix each command with `uv run --env-file .env` so the tests find PostgreSQL and Valkey (`docs/development/without-docker.md#checks`):

```bash
python manage.py spectacular --settings config.settings.test --file docs/openapi.yaml --validate --fail-on-warn
scripts/check.sh
```

`scripts/check.sh` runs ruff, the `cache.clear()` guard, mypy, django-upgrade, the OpenAPI diff, `makemigrations --check`, and pytest with coverage. After a dependency change: `uv lock`, then `uv sync --locked` in the container. Commits follow Conventional Commits in English (`feat:`, `fix:`, `test:`, `docs:`), imperative mood.

## When the endpoint also enqueues work

Keep this skill for the HTTP surface. For the task itself follow [tasks.md#writing-a-task](../../../docs/tasks.md#writing-a-task): thin `@shared_task` with `name="<app>.<task>"`, IDs and JSON kwargs only, `enqueue_on_commit`, idempotency, and the enqueue tests in AGENTS.md (service unit test, `task.apply`, `django_capture_on_commit_callbacks` + patched `apply_async`). The API returns `202 Accepted` and a resource URL; the client polls it. Do not import `django.tasks`.

## Do not

- Put business logic in views or tasks.
- Return or accept integer primary keys.
- Cache in the view, use `cache_page`, or call `cache.clear()` (it is `FLUSHDB` on Valkey).
- Enqueue a task or send email / invalidate cache inside `atomic` without `on_commit`.
- Set `throttle_classes` in a way that drops the global throttles.
- Add `gettext_lazy`, HTML API pages, or a `docs/glossary.md`.
- Translate or rename anything under `bruno/`.
- Change coverage `fail_under` (it stays at 98).
- Copy decisions from some other product's domain; this recipe is generic.
