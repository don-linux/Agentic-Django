---
name: add-model-endpoint
description: Add a domain model and its /api/v1 endpoint using this template's layout — BaseModel with UUIDv7 public_id and timestamps, keyword-only services, selectors (cached when the read is hot), Pydantic schemas, Ninja operations with declared error responses, throttle scope, OpenAPI, tests including negative paths, a Bruno request, and a named migration. Use when the user asks to add a model, a resource, a CRUD or list/detail endpoint, or a new API under /api/v1.
---

# Add a model and its API endpoint

Follow this checklist in order. Mandatory rules stay in [AGENTS.md](../../../AGENTS.md); this skill is the recipe. The "why" and the numbers (TTLs, page sizes, rates) live in the linked docs — do not copy them back into AGENTS.md or into this file.

Worked example in this template (read it; do not clone User blindly): `apps/users/` — `models.py`, `services.py`, `selectors.py`, `signals.py`, `api/views.py` (schemas and operations), `tests/`, and the router mount in `config/api.py`.

`User` is a special case: it builds on `AbstractBaseUser` and `PermissionsMixin` (not `AbstractUser`; see `docs/decisions/0014-user-extends-abstractbaseuser-and-permissionsmixin.md`) and declares its own `public_id`. **New domain models inherit `core.models.BaseModel`.** They do not copy User's inheritance.

## When to run

Run when the user wants a new persisted resource exposed under `/api/v1/`.

If `pyproject.toml` still has `name = "project"` **and** the user is starting a product from this template, stop and run [personalize-django-template](../personalize-django-template/SKILL.md) first. If they explicitly asked to add a model on this still-generic tree (for example while editing the template itself), continue.

If the work is only a background job, a file upload, or an opt-in recipe (append-only table, audit log, field encryption), follow the area doc instead of this whole checklist, then return here for any HTTP surface.

## Before writing code

1. Translate any Spanish (or other non-English) domain name yourself into an English identifier, consistent with existing models. Example: "documentos casa" → `HouseDocument`, route `/api/v1/house-documents/`, table/app style `house_documents` / a short English plural app such as `documents` — not `DocumentosCasa`. There is no glossary; do not ask the user to pre-register the term, and do not create `docs/glossary.md`. Rule: [AGENTS.md](../../../AGENTS.md) Language and [.agents/rules/only-english-code.md](../../rules/only-english-code.md).
2. Pick the app (`apps/<app>/`) or create one. Layout: `models.py`, `services.py` (writes), `selectors.py` (reads), `api/views.py` (its schemas and operations; split out `api/schemas.py` only when it grows), `tests/` (`tests/services/`, `tests/selectors/`, `tests/http/` for the HTTP contract, `tests/ninja/` only for tests that need Django Ninja itself, plus `test_models.py` when the model has real behaviour), and `tests/factories.py` (one factory-boy factory per model; no JSON fixtures). `AppConfig.name` must be `"apps.<app>"` (never bare `"<app>"`), and `"apps.<app>"` must be listed in `LOCAL_APPS` (`config/settings/base.py`). Import signals from `AppConfig.ready()` when the app has receivers. Shared helpers stay in `core/`; do not put domain code there.
3. Read [docs/ARCHITECTURE.md](../../../docs/ARCHITECTURE.md#layers), [performance](../../../docs/ARCHITECTURE.md#performance), [indexes](../../../docs/ARCHITECTURE.md#indexes), [caching](../../../docs/ARCHITECTURE.md#caching), and [docs/api.md](../../../docs/api.md) (`#paths`, `#representation`, `#throttling`, `#error-codes`, `#pagination`).
4. Show a short plan (English names, model fields, routes, who may see a row, whether the read is cached, whether a write starts a task) and wait for confirmation if the user asked for a plan first. Do not break the Domain invariants listed in `AGENTS.md`.

## 1. Model

In `apps/<app>/models.py`:

- Subclass `core.models.BaseModel`. That gives `public_id` (UUIDv7, unique, non-editable), `created_at` and `updated_at` from the database clock. Custom managers subclass `core.models.BaseManager`.
- Annotate every field with its django-stubs generic type: `title: models.CharField[str, str] = models.CharField(max_length=120)`, `owner: models.ForeignKey[User, User] = models.ForeignKey(User, on_delete=models.PROTECT)`, `str | None` on the read side for `null=True`. `core/tests/test_model_field_annotations.py` fails on a bare field. Why: [typing.md#model-field-annotations](../../../docs/typing.md#model-field-annotations).
- `class Meta(BaseModel.Meta):` inherits the abstract base's options and needs no suppression; a factory's `class Meta:` does ([typing.md#suppressions](../../../docs/typing.md#suppressions)).
- mypy checks `models.py` and `managers.py` in strict mode, as it does services and selectors: no `Any`. A keyword argument passed through untouched is `object` (`**extra_fields: object`), and an override of `save()` repeats Django's keyword-only signature (`core.models.BaseModel.save` is the pattern), marked `@override` ([ARCHITECTURE.md#strict-typing](../../../docs/ARCHITECTURE.md#strict-typing)).
- Put invariants in database constraints where possible (`CheckConstraint`, `UniqueConstraint(..., condition=Q(...))` for partial uniqueness). Services will call `full_clean()` as well.
- Foreign keys default to `on_delete=models.PROTECT`. Use `CASCADE` only for a child owned by its parent, and add the field to `CASCADE_ALLOWED` in `core/tests/test_on_delete_policy.py` with a reason. Use `SET_NULL` only when losing the link is acceptable. Why: [ARCHITECTURE.md#layers](../../../docs/ARCHITECTURE.md#layers).
- Records that must persist are deactivated, not deleted, unless the on-delete policy already covers a true child row.
- File and image fields use `core.files.UploadTo` and `MaxFileSizeValidator`, store a generated name (never the client filename), and must not accept unsanitized HTML/SVG. Why: [files.md#field](../../../docs/files.md#field).
- Sensitive values follow the encryption recipe in [security.md#recipe-encrypting-sensitive-fields](../../../docs/security.md#recipe-encrypting-sensitive-fields) only when the project opts in.
- No `gettext_lazy`. End-user strings are plain English (`USE_I18N = False`).
- Default list ordering that is not the primary key, every ordering a client may choose, and every list filter must be indexed. Name indexes `<app>_<model>_<cols>_idx`. `BaseModel.created_at` is **not** indexed: `order_by("id")` is safe; `order_by("-created_at")` needs a named index. Why: [ARCHITECTURE.md#indexes](../../../docs/ARCHITECTURE.md#indexes).

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
- Business logic lives here. Operations stay thin: the schema validates, the operation calls one service or selector and returns its result.
- Side effects (cache invalidation, Celery, email, webhooks, files) run in `transaction.on_commit(...)`. Use `core.cache.invalidate("<app>.<resource>")` and `core.tasks.enqueue_on_commit(task, **kwargs)` — they already defer to commit. Never call `task.delay()`, `apply_async()`, or `delay_on_commit()`.
- Read-modify-write on the same row uses `select_for_update()` inside the service transaction (counters, balances, single-use codes).
- Business rejections raise `core.exceptions.ApplicationError` (or `ConflictError` for 409) with a stable `snake_case` code. Register a new code in [api.md#error-codes](../../../docs/api.md#error-codes) when it is first used. Never raise a Ninja or Django HTTP exception for a domain rule. Do not catch `ValidationError` in the view; the API exception handlers already map it.
- A unique field is checked in the service, before `full_clean()`: a duplicate raises `ConflictError(..., code="already_exists", extra={"field": "<field>"})`, a 409. `full_clean()` would answer 400 to a request the schema allows, and the conformance test fails with ST202 ([api.md#duplicates](../../../docs/api.md#duplicates)).
- `objects.filter(...).update(...)` already bumps `updated_at`. `bulk_update()` does not: include `updated_at` in its fields. After `queryset.update()`, `bulk_create()`, `bulk_update()`, or `RunPython`, call `invalidate()` yourself (no signals fire). If the selector is cached, also add `post_save` / `post_delete` receivers in `apps/<app>/signals.py` for admin and other non-service writes.

## 4. Selectors (reads)

In `apps/<app>/selectors.py`:

- Every read that is more than a plain lookup lives here. The selector owns the query shape: `select_related()` / `prefetch_related()` (or `Prefetch(...)`) for everything the output schema reads. Operations call the selector and return its queryset or object. Counts and sums come from `annotate()` in the selector.
- Order list querysets deterministically, on an indexed column, and add the selector to `core/tests/ninja/test_list_indexes.py`, which checks the ordering of each selector it lists (it does not find new ones). Why: [ARCHITECTURE.md#performance](../../../docs/ARCHITECTURE.md#performance), [#indexes](../../../docs/ARCHITECTURE.md#indexes).
- Look objects up by `public_id`, never by integer pk.
- Scope the queryset to the requesting user or tenant.
- Do not query `request.user` again; authentication already loaded it.

### When the read is hot or expensive

Wrap the query in `core.cache.read_through("<app>.<resource>", *parts, ttl=..., loader=...)`. Cache in the selector, never in the view, so authentication, permissions and throttles always run first.

- Every key has a positive `ttl` in seconds (`read_through` has no default and rejects `ttl <= 0`). Pick the value from [ARCHITECTURE.md#caching](../../../docs/ARCHITECTURE.md#caching). Never call the Django cache API with `timeout=None`.
- Data that depends on the user or tenant includes `user.pk` (or the tenant id) in the key parts. Never cache per-user data under a shared key. Never put raw client input, emails, tokens or other PII in a key.
- Prefer plain data (dicts, lists, primitives, `Schema.model_dump(mode="json")` output) over model instances. If a namespace caches instances, bump a namespace version when the model's fields change (a cached instance is a pickle of its field values) and defer secret fields. Worked example: `user_get` in `apps/users/selectors.py` (`USER_CACHE_VERSION`, `.defer("password")`).
- Do not cache `request.user`, authentication, permission checks, or data that must be strictly consistent (balances, stock, single-use codes).
- If you cache, also add `apps/<app>/signals.py` with `post_save` / `post_delete` receivers that call `invalidate("<app>.<resource>")`. Import the module from `AppConfig.ready()`.

Why: [ARCHITECTURE.md#caching](../../../docs/ARCHITECTURE.md#caching). Outages: [security.md#valkey-outages](../../../docs/security.md#valkey-outages).

## 5. Schemas

In `apps/<app>/api/views.py`, above the operations (pattern: `UserOut` and `UserUpdateIn` in `apps/users/api/views.py`). pyright checks `apps/<app>/api/` in strict mode, so a validator gives its raw `Any` input a real type before using it ([typing.md#strict-api](../../../docs/typing.md#strict-api)):

- Output schemas subclass `core.api.schemas.BaseSchema`; input schemas subclass `core.api.schemas.InputSchema`, so an unknown body key is a 400 with code `unknown_field`, not a silent ignore ([Ninja: schemas](https://django-ninja.dev/guide/schemas/)). Every other field error keeps Pydantic's own type and message (`missing`, `string_type`, `string_too_long`…): do not translate them ([api.md#error-body](../../../docs/api.md#error-body)).
- Expose `public_id` as `id`, a string, read-only: `id: UUID = Field(json_schema_extra={"readOnly": True})` with a `resolve_id(obj) -> UUID` static method that returns `obj.public_id` ([Ninja: resolvers](https://django-ninja.dev/guide/schemas/#resolvers)). Mark every other server-set field `readOnly` the same way.
- Datetime fields use `core.api.schemas.UTCDateTime` and date fields `core.api.schemas.ISODate`: a request sends RFC 3339 (a datetime with its time zone), and `TIME_ZONE` must not change the JSON (`Z` suffix, UTC). A UUID a request sends, such as a related object's id, is `core.api.schemas.PublicId`.
- An input schema states every rule of the model that a schema can express: `max_length=` from the model field, and `min_length=1` for a `CharField` without `blank=True`. Otherwise `full_clean()` answers 400 to a request the schema allows, and the conformance test fails with ST202. `InputSchema` already refuses NUL and lone surrogates in every string ([api.md#field-types](../../../docs/api.md#field-types)).
- Integer, number and boolean fields of an input schema are Pydantic's `StrictInt`, `StrictFloat` and `StrictBool`; a query parameter keeps `int`, `FiniteFloat` and `bool`, which parse text. A plain `int` in a body takes `"1"` and `true`, which the schema forbids and the conformance test sends. Do not set `ConfigDict(strict=True)`: Ninja validates in Python mode, where strict refuses the strings a datetime or a UUID arrives as ([api.md#field-types](../../../docs/api.md#field-types)).
- Every field the output always renders is required in the schema (no default), so oasdiff sees its removal ([api.md#breaking-changes](../../../docs/api.md#breaking-changes)).
- A `PATCH` input schema has every field optional; read only the keys the client sent with `body.model_dump(exclude_unset=True)`.
- Why: [api.md#representation](../../../docs/api.md#representation).

## 6. Operations and router

In `apps/<app>/api/views.py`:

- `router = Router(tags=["<app>"])`. Each operation validates through its schema, calls one service or selector, and returns the model, queryset or `Status(...)`. Do not wrap those calls in `try/except ValidationError`.
- Every operation declares `response={<2xx>: Schema, **<ERRORS>}`, `openapi_extra=response_docs(<ERRORS>)` with the same map, an explicit `operation_id="<app>_<resource>_<action>"` and a `summary`. Pick the map from `core.api.responses`: `LIST_ERRORS` for a list, `LOOKUP_ERRORS` for a detail, `WRITE_ERRORS` for a body (combine `{**LOOKUP_ERRORS, **WRITE_ERRORS}` on `PATCH`), plus `CONFLICT_ERRORS` on an operation that can create a duplicate and on the delete of a model that `PROTECT` foreign keys point to. Why: [api.md#error-responses-in-openapi](../../../docs/api.md#error-responses-in-openapi); `core/tests/ninja/test_openapi_responses.py` fails when one is missing.
- Detail paths are `"/{public_id:id}/"` with a `<model>_id: PathEx[UUID, P(alias="id")]` argument: the alias keeps the contract's `id` without shadowing the builtin, and the `public_id` converter accepts only a UUID (either case), so an integer pk is a plain 404 before the body is parsed. Why: [api.md#paths](../../../docs/api.md#paths).
- Lists use `@paginate(LimitPageNumberPagination)` (or `LimitCursorPagination`) under the route decorator and return the selector's ordered queryset ([api.md#pagination](../../../docs/api.md#pagination)).
- Create: take `response: HttpResponse` as an argument, set `response["Location"]` to the new object's `url`, built as the output schema's `resolve_url` builds it (`request.build_absolute_uri(reverse("api:<resource>-detail", kwargs={"id": obj.public_id}))`, which keeps the script prefix), and `return Status(201, obj)` ([Ninja: setting headers](https://django-ninja.dev/guide/temporal-response/#setting-headers)). Delete: `response={204: None, **LOOKUP_ERRORS}` (plus `**CONFLICT_ERRORS` when `PROTECT` foreign keys point to the model) and `return Status(204, None)`. Annotate an operation that returns `Status` as `-> Any`.
- Scope every read and write to the caller. `API_AUTH` already rejects anonymous requests with 401, but `request.user` is still typed `User | AnonymousUser`: narrow it once with `core.api.auth.request_user(request, User)` and pass the result to the selector or service. Do not `cast()`. A detail lookup is a selector scoped to the caller that raises `DoesNotExist` for an object they cannot see, which the API answers with 404 (pattern: `apps.users.selectors.user_visible`).
- Declare only the methods the resource supports. `HEAD`, `OPTIONS`, `Allow` and 405 come from `ApiContractMiddleware` for every route; nothing to register ([api.md#head-options-and-405](../../../docs/api.md#head-options-and-405)). An undeclared `PUT` is 405.
- The body parameter may have any name (`body` is the convention); errors are keyed by field name without it.
- Mount the router in `config/api.py`, next to the users one, as `api.add_router("/<resource>/", <app>_router)`. Not in `config/urls.py`: a test that reloads the URLconf would mount it again, and Ninja refuses that. Do not add a second API instance, an API root, or a route under another prefix.
- Static path segments are kebab-case and end with `/` (`"/{public_id:id}/change-password/"`, never `change_password`). At most one level of nesting. A path without the trailing slash must 404 (not redirect). Enforced by `core/tests/http/test_rest_conventions.py`. Why: [api.md#paths](../../../docs/api.md#paths).
- Everything stays on `/api/v1/`, the template's only API: make every change compatible, and deprecate before removing ([api.md#deprecating-endpoints-and-fields](../../../docs/api.md#deprecating-endpoints-and-fields)).
- Create endpoints that clients may retry (mobile, lost response) follow [api.md#idempotent-retries](../../../docs/api.md#idempotent-retries). A first internal POST does not have to implement `Idempotency-Key`; say so in the plan if you skip it.
- Responses are `no-store` by default (`ApiNoStoreMiddleware`). Never `cache_page` or the per-site cache middleware. Why: [api.md#caching](../../../docs/api.md#caching).

## Model with a file

When the new model has a `FileField` or `ImageField`, also follow [docs/files.md](../../../docs/files.md) — do not stop at the JSON checklist.

- User-sensitive files use `storage=private_storage`, never serialize `.url`, and are downloaded through a kebab-case operation (`/download/`) that checks the owner-scoped queryset and returns `protected_file_response(...)`.
- The create/upload operation is the only one that accepts `multipart/form-data`: `file: File[UploadedFile]` and `Form[...]` parameters, both imported from `ninja` ([docs/files.md#endpoint](../../../docs/files.md#endpoint)). Every operation with a JSON body still answers a multipart body with 415.

- Services that replace or delete a file call `core.files.delete_file_on_commit` before saving or deleting the row.
- If the story does not name allowed extensions or a size cap, pick a tight allow-list (never unsanitized HTML/SVG) and a cap at or below the proxy `client_max_body_size`, and state the choice. Do not invent a `TextChoices` enum for an unspecified "tipo".
- After regenerating `docs/openapi.json`, `apps/users/tests/ninja/test_openapi.py` must allow `multipart/form-data` only on those upload operations (not `== {"application/json"}` for the whole schema).

## 7. Throttle scope

Global anon/user throttles are already on. For a sensitive or expensive operation:

- Pass `throttle=[AnonThrottle(), UserThrottle(), ScopedThrottle(scope="<app>.<action>")]` (all from `core.api.throttling`) on the route decorator. `throttle=` replaces the API-level throttles, so keep the first two ([Ninja: router and operation level](https://django-ninja.dev/guide/throttling/#router-and-operation-level)).
- Register the rate in `NINJA_DEFAULT_THROTTLE_RATES` in `config/settings/base.py`. An unregistered scope raises `ImproperlyConfigured` when the module is imported, so every test fails until it is registered.
- Why: [api.md#throttling](../../../docs/api.md#throttling).

## 8. OpenAPI

The `response=` maps and `openapi_extra=response_docs(...)` from step 6 are what document the new paths, error bodies and headers ([api.md#error-responses-in-openapi](../../../docs/api.md#error-responses-in-openapi)). Swagger lives at `/api/v1/docs/` (staff). `core/tests/ninja/test_openapi_responses.py` checks every operation declares a 2xx and the common errors and that each status seen at run time is declared.

After any API change, regenerate the committed contract with test settings (never hand-edit `docs/openapi.json`).

From the host, using the dev compose service (the command writes `docs/openapi.json` as your host user, whatever `HOST_UID` the container runs with):

```bash
docker compose -f docker/dev/compose.yaml exec -T django python manage.py export_openapi_schema --settings config.settings.test --api config.api.api --indent 2 > docs/openapi.json
```

If you are already inside the container, or on a host with the toolchain (`uv run`, as CI does):

```bash
python manage.py export_openapi_schema --settings config.settings.test --api config.api.api --indent 2 > docs/openapi.json
```

`scripts/check.sh lint` and `core/tests/ninja/test_openapi_contract.py` fail when the file drifts. A diff that removes or renames a path, field, or enum value is a breaking change, and the `api-contract` job refuses it: add the new one next to the old one and deprecate the old one. Check it with oasdiff as CI does, including `--flatten-allof` ([api.md#breaking-changes](../../../docs/api.md#breaking-changes)).

## 9. Tests (including negative paths)

Put them under `apps/<app>/tests/` ([test layout](../../../docs/development.md#test-layout)). API tests go in `apps/<app>/tests/http/`: they hit literal `/api/v1/...` paths with `django.test.Client` (`client`, `user_client`), assert the status, the error `code`, and the body shape, and never import `ninja`. Only a test of a schema, a router hook or the OpenAPI document goes in `apps/<app>/tests/ninja/`. Use the root `conftest.py` fixtures and the model factory. Imports used only as annotations go under `TYPE_CHECKING` (ruff `TC001` / `TC002`), including `django.test.Client` in test signatures. Import factory-boy names from its submodules (`factory.django`, `factory.faker`, and `factory.declarations` for `SubFactory`), and copy the `class Meta:` suppression and the typed `__new__` stub from `apps/users/tests/factories.py`; why they are there: [typing.md#upstream-workarounds](../../../docs/typing.md#upstream-workarounds).

Every new endpoint needs:

- Happy path (create/list/retrieve/update as exposed).
- `401` unauthenticated (`WWW-Authenticate: X-Session-Token`).
- `403` only when a permission other than "must be logged in" applies. Another user's object is `404`, not `403`, when the queryset is scoped.
- `404` for another user's object, for an unknown `public_id`, and for an integer pk if the detail route uses `public_id`.
- `400` validation: unknown field, `full_clean()` failure surfaced as JSON, and any domain `ApplicationError`.
- An exact query budget (`django_assert_num_queries`). Measure it once with a **session-authenticated** client (LocMem cache + `cached_db` sessions, as in `apps/users/tests/http/test_query_budget.py`), pin the number, and comment what each query is — `full_clean()` often adds existence checks, and a `CheckConstraint` can add a savepoint plus a check query. If the assertion does not print the SQL, dump it with `CaptureQueriesContext`. Do not use a framework test client helper that bypasses the real authentication path for the budget; use a session-authenticated `user_client`.
- Every list endpoint also gets `assert_constant_queries` that grows related data.
- A service test for the `full_clean()` failure.

If the selector is cached, also test: a second read makes zero queries (`cache_writes` + `django_assert_num_queries(0)`), a write through the service returns fresh data, and a rolled-back write leaves the cache untouched. Behaviour that only a real Valkey can show belongs in `core/tests/test_valkey_integration.py` (marker `valkey`).

`core/tests/test_openapi_conformance.py` already sends the new operations requests generated from the schema (schemathesis), and fails on any response the schema does not describe. Fix the server or the schema: the test has no exceptions ([api.md#contract-conformance](../../../docs/api.md#contract-conformance)).

If the operation has a scoped throttle, test that the N+1th call returns `429` `throttled`. Set the rate with the `throttle_rates` fixture (`core/tests/ninja/adapters.py`); a test under `tests/http/` never patches Ninja itself.

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
docker compose -f docker/dev/compose.yaml exec -T django python manage.py export_openapi_schema --settings config.settings.test --api config.api.api --indent 2 > docs/openapi.json
docker compose -f docker/dev/compose.yaml exec django scripts/check.sh
```

Inside the container. On the host, prefix each command with `uv run --env-file .env` so the tests find PostgreSQL and Valkey (`docs/development/without-docker.md#checks`):

```bash
python manage.py export_openapi_schema --settings config.settings.test --api config.api.api --indent 2 > docs/openapi.json
scripts/check.sh
```

`scripts/check.sh` runs ruff (with the `ANN` annotation rules), the `cache.clear()` guard, mypy, pyright, the import contracts, django-upgrade, the OpenAPI drift check, `makemigrations --check`, and pytest with coverage. Both type checkers must pass with no new suppression, pyright in strict mode on `apps/<app>/api/`, and every method that overrides a base class's (`save`, `clean`, admin hooks) carries `@override` from `typing` ([typing.md#overrides](../../../docs/typing.md#overrides)); when one is unavoidable, scope it to the rule and record it in [typing.md#upstream-workarounds](../../../docs/typing.md#upstream-workarounds). After a dependency change: `uv lock`, then `uv sync --locked` in the container. Commits follow Conventional Commits in English (`feat:`, `fix:`, `test:`, `docs:`), imperative mood.

## When the endpoint also enqueues work

Keep this skill for the HTTP surface. For the task itself follow [tasks.md#writing-a-task](../../../docs/tasks.md#writing-a-task): thin `@shared_task` with `name="<app>.<task>"`, IDs and JSON kwargs only, `enqueue_on_commit`, idempotency, and the enqueue tests in AGENTS.md (service unit test, `task.apply`, `django_capture_on_commit_callbacks` + patched `apply_async`). The API returns `202 Accepted` and a resource URL; the client polls it. Do not import `django.tasks`.

## Do not

- Put business logic in views or tasks.
- Return or accept integer primary keys.
- Cache in the view, use `cache_page`, or call `cache.clear()` (it is `FLUSHDB` on Valkey).
- Enqueue a task or send email / invalidate cache inside `atomic` without `on_commit`.
- Pass `throttle=` without `AnonThrottle()` and `UserThrottle()`.
- Add `gettext_lazy`, HTML API pages, or a `docs/glossary.md`.
- Translate or rename anything under `bruno/`.
- Change coverage `fail_under` (it stays at 98).
- Copy decisions from some other product's domain; this recipe is generic.
