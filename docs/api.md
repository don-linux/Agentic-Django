# API conventions

Rules every endpoint under `/api/v1/` follows. Clients branch on `code`, never on `detail`. Account endpoints under `/_allauth/` belong to django-allauth and follow its own contract ([Out of scope](#out-of-scope-_allauth)).

## Resources and HTTP semantics

### Paths

- Plural nouns, lowercase, with kebab-case static segments: an extra action is a path segment such as `"/{public_id:id}/change-password/"`, never `change_password`. At most one level of nesting (`/projects/{id}/members/`), and `/users/me/` for the current user. Query parameters and JSON keys are `snake_case`. `core/tests/http/test_rest_conventions.py` fails on a route that breaks the naming.
- A detail path is `"/{public_id:id}/"`. The view takes it as `<model>_id: PathEx[UUID, P(alias="id")]`: the alias keeps the contract's `id` without an argument that shadows the builtin `id` ([Ninja: path parameters](https://django-ninja.dev/guide/path-params/); `apps/users/api/views.py`).
- Paths always end with `/`. A path without the trailing slash returns 404; it is never redirected. A 301 would make most HTTP clients repeat a `POST` as a `GET` and report success without creating anything (`core.middleware.ApiCommonMiddleware`). Other paths, such as the admin, keep Django's slash redirect.

With `DEBUG=True`, Django raises `RuntimeError` (a 500) for a POST, PUT, PATCH or DELETE without the trailing slash instead of redirecting; `ApiCommonMiddleware` makes it a 404 everywhere. Each app exposes a Ninja `Router` mounted on the one `NinjaAPI` in `config/api.py`, which `config/urls.py` serves under `/api/v1/` ([Ninja: routers](https://django-ninja.dev/guide/routers/)); there is no API root view and no format suffix.

### Methods and status codes

| Method | Success | Notes |
| --- | --- | --- |
| `GET` | 200 | |
| `HEAD`, `OPTIONS` | 200 | Automatic on every route ([below](#head-options-and-405)); `OPTIONS` lists the methods in `Allow` |
| `POST` (create) | 201 | `Location` points to the new resource. Set it from the output schema's `url` field |
| `POST` (action) | 200, or 202 | 202 when the work continues asynchronously |
| `PATCH` | 200 | Partial update; returns the updated resource |
| `PUT` | 200 | Only for a full replacement with every writable field required; otherwise do not declare it, and `PUT` answers 405 |
| `DELETE` | 204 | No body |

Errors use the [error body](#error-body): 400, 401 (with `WWW-Authenticate`), 403, 404 (also for another user's object), 405 (with `Allow`), 406, 409, 415, and 429 (with `Retry-After`).

A request without valid credentials gets 401 with `WWW-Authenticate: X-Session-Token`, never 403: the `AuthenticationError` handler in `core.api.exceptions` sets the header. `core.api.auth.API_AUTH` tries the `X-Session-Token` first and the staff session cookie second ([Ninja: multiple authenticators](https://django-ninja.dev/guide/authentication/#multiple-authenticators)); a cookie write without a CSRF token is 403 `permission_denied`.

#### HEAD, OPTIONS and 405

`core.api.negotiation.ApiContractMiddleware` answers these for every route under `/api/v1/`, so an operation declares only its real method. Ninja's guide adds them with `api_operation(["HEAD", "OPTIONS"], ...)` per route ([Ninja: handling multiple methods](https://django-ninja.dev/guide/operations/#handling-multiple-methods)), which would repeat every route and publish duplicate `operationId`s.

- The methods come from the operations registered on the API (its OpenAPI document), so a new router needs no entry anywhere. `Allow` lists them in the order `GET, POST, PUT, PATCH, DELETE`, then `HEAD` when there is a `GET`, then `OPTIONS`, and is sent on every response of the route.
- A method the route does not declare is 405 `method_not_allowed` with `Allow`, before authentication. A path that matches no route is 404.
- `HEAD` runs the `GET` operation without a body.
- `OPTIONS` is 401 without credentials, else 200 with `{"name", "description", "renders", "parses"}`. On `docs/` and `schema/` it keeps the staff check.

### Representation

- One resource is a bare JSON object; a list is the pagination envelope (`count`, `next`, `previous`, `results`, with absolute links).
- Every resource exposes its public UUID as `id` (a string). Integer primary keys never leave the server, including `/_allauth/` payloads (`apps.users.adapters.HeadlessAdapter`). Detail URLs use the same UUID (`/api/v1/users/{id}/`); an integer in that position is a plain 404.
- Datetimes are ISO 8601 in UTC with a `Z` suffix, whatever `TIME_ZONE` is. A naive datetime in a request is read as UTC. Declare datetime fields as `core.api.schemas.UTCDateTime`; a plain `datetime` keeps the offset it was given and loses microseconds in the JSON encoder. Dates are `YYYY-MM-DD`. Clients convert to local time; `TIME_ZONE` only drives date boundaries and reports.
- Decimals are strings, and declared fields are always present (`null`, never omitted).
- `Content-Type: application/json`, without a charset parameter: JSON is UTF-8 by definition.
- Request bodies are `application/json` only: `core.api.parsers.JSONParser.parse_body` answers a non-empty body with another `Content-Type` with 415 `unsupported_media_type` (Ninja itself does not inspect it, [Ninja: request parsers](https://django-ninja.dev/guide/parsers/)), and the schema advertises only JSON. An upload operation takes `File`/`Form` parameters instead of a body, so it accepts multipart ([files.md](files.md#endpoint)).
- A request body key that is not a field of the endpoint is a 400 `validation_error` whose `fields` entry has the code `unknown_field`, not a silently ignored key. Read-only fields (`id`, `url`) are dropped only where the input schema drops them before validation (`UserUpdateIn.drop_read_only` in `apps/users/api/views.py`), so a client can send a `GET` body back in that `PATCH`; any other input schema answers them with `unknown_field`.
- Content negotiation uses the `Accept` header only: `ApiContractMiddleware` answers an `Accept` the API cannot produce with 406 `not_acceptable`. As in DRF, that comes first on a route that exists, before the 405, `OPTIONS` and authentication; a path that is not a route is 404 whatever the `Accept`. The most specific media range wins, then the route's own order. q values are ignored, except `q=0`, "not acceptable" (RFC 9110, section 12.4.2): a media type is not acceptable when the most specific ranges that cover it (`type/subtype` with parameters, then `type/subtype`, `type/*` and `*/*`; section 12.5.1) all have `q=0`, so `*/*, application/json;q=0` is 406 on a JSON resource, and a range with `q=0` never chooses the response. A range with `q=0` and parameters other than `q` refuses only the variant with those parameters (section 12.5.1, table 5, as Django's `MediaType.match`), so `application/json, application/json;indent=2;q=0` is JSON; above 0, as in DRF's matching, a range covers a media type whatever its parameters. Of two ranges equally specific, one above 0 and one with `q=0`, the media type is acceptable: RFC 9110 does not define that case. `q="0"` and `q = 0` are `q=0` too; `q=00` is not a valid qvalue and, like other weights, is ignored. As in DRF 3.18.3, only the first 64 members of the `Accept` header count, a member longer than 256 characters is skipped, and a header longer than 8192 characters is cut at its last comma before that length. A present but empty `Accept` is 406, and so is a range like `*/json`, which RFC 9110 (section 12.5.1) does not allow. `?format=` is ignored, so it never changes a response or leaks into hyperlinks. The schema at `/api/v1/schema/` is YAML by default (`application/vnd.oai.openapi`); `application/yaml`, `application/vnd.oai.openapi+json` and `application/json` answer with that same `Content-Type`, the JSON indented 4 (or `; indent=N`, at most 8), as drf-spectacular rendered it, but without its `Content-Disposition` header. The resources' JSON is always compact: unlike DRF's `JSONRenderer`, they ignore `; indent=N`. Staff `OPTIONS` on the schema returns its metadata in that format; on `/api/v1/docs/` it is JSON metadata. Generate the schema file with `python manage.py export_openapi_schema --api config.api.api`.

`apps.users.adapters.HeadlessAdapter` overrides `get_user_dataclass` and `user_as_dataclass` (not `serialize_user`), so allauth's user payload carries the public UUID as `"id"` and its schema stays in sync. The schema and Swagger UI at `/api/v1/docs/` are staff-only, and production fills the schema's `servers` from `ALLOWED_HOSTS`.

### Caching

API responses are `no-store` by default (`core.middleware.ApiNoStoreMiddleware`). A view that serves public, non-personal data may set its own `Cache-Control`; never cache responses that depend on the session.

Server-side caching happens in selectors (`core.cache.read_through`, on Valkey), after authentication, permissions and throttling have run. `cache_page` and the per-site cache middleware are banned: `X-Session-Token` is not in `Vary`, so one user's response would be served to another, and a cache hit would skip permissions and throttles. The rules are in AGENTS.md, "Caching (Valkey)", and the design in [ARCHITECTURE.md](ARCHITECTURE.md#caching).

`ApiNoStoreMiddleware` sits right after `SecurityMiddleware` and sends the same headers as allauth's `never_cache`, because shared caches do not treat `X-Session-Token` like `Authorization`.

### Why not RFC 9457

The API keeps `{"code", "detail"}` with `application/json`. `detail` already has the RFC 9457 meaning, and `code` plays the role of `type`. Switching to `application/problem+json` would break clients that match on `application/json`; adding `type`, `title`, or `status` members later would be a compatible change.

## Pagination

- Default: `core.api.pagination.LimitPageNumberPagination`. `?page=` and `?page_size=` (20 by default, at most 100); the response is `count`, `next`, `previous`, `results`. Every page runs `COUNT(*)` plus `OFFSET`, which slows down with depth, so pages deeper than `DJANGO_API_MAX_PAGE_NUMBER` (1000) are a `404 not_found`, like any page past the end. Use it for admin-like lists where the total matters.
- Opt-in: `@paginate(LimitCursorPagination)` from `core.api.pagination` ([Ninja: CursorPagination](https://django-ninja.dev/guide/pagination/#cursorpagination)) for large or append-heavy tables, feeds, and infinite scroll. Clients follow the `next`/`previous` links (`?cursor=`); there is no `count`, and every page costs the same at any depth. It orders by `-public_id`; a custom `ordering` must be unique and indexed. An invalid page number (`?page=abc`, `0`, or `-1`) on a page-number list is a `404` with detail `Invalid page.`, like a page past the end. An invalid cursor (`?cursor=not-a-cursor`) on a cursor-paginated endpoint is Ninja's `400` validation error; it is not mapped to `404`, and `/api/v1/users/` does not use cursor pagination.
- Opt-in: `core.api.pagination.CachedCountPageNumberPagination` for page-number lists over a large table, where `COUNT(*)` is the expensive query of every page. It caches the total of each query in Valkey for 60 seconds, so `count` and the last page number may lag by up to a minute, or less when the service that writes the model calls `core.cache.invalidate()`. The key is the SQL of the queryset, so each user's or tenant's list has its own total. The rows of a page are never cached, and cursor pages have no count to cache.

## Idempotent retries

A mobile client retries a `POST` when the response is lost, so a create endpoint that clients may retry must not create the resource twice:

- The client sends an `Idempotency-Key` header, a UUID it generates once per logical operation. Alternatively the resource has a natural unique key, such as an external reference.
- The service stores the key on the resource under a `UniqueConstraint` scoped to the caller (for example `fields=["owner", "idempotency_key"]`), inside `transaction.atomic()`.
- The first call returns `201 Created`. A retry with the same key and the same payload returns the existing resource with `200 OK`.
- The same key with a different payload raises `ConflictError(..., code="idempotency_key_reused")` (409).
- Browsers send a custom header cross-origin only when the server allows it: add `idempotency-key` to `CORS_ALLOW_HEADERS` when the first endpoint uses it.

## Error body

Every error response from an API view has this JSON body:

```json
{"code": "not_found", "detail": "Not found."}
```

- `code`: a stable `snake_case` identifier listed in [Error codes](#error-codes).
- `detail`: an English message for humans. It can change between releases.
- `extra` (optional): structured context for a domain error, for example `{"available": 2}`.

Validation errors (`400`) add `fields`, with one list of `{code, detail}` items per field. Nested schemas keep their nesting, list items are keyed by index, and errors that belong to no single field go under `non_field_errors`:

```json
{
  "code": "validation_error",
  "detail": "Invalid input.",
  "fields": {
    "name": [{"code": "max_length", "detail": "Ensure this field has no more than 255 characters."}],
    "address": {"city": [{"code": "required", "detail": "This field is required."}]},
    "non_field_errors": [{"code": "invalid", "detail": "Cross-field rule failed."}]
  }
}
```

### Examples by status

| Status | Example body | Notes |
| --- | --- | --- |
| 400 | `{"code": "parse_error", "detail": "JSON parse error - ..."}` | Malformed body; field errors use `validation_error` (above) |
| 401 | `{"code": "not_authenticated", "detail": "Authentication credentials were not provided."}` | Sent with `WWW-Authenticate: X-Session-Token` |
| 403 | `{"code": "permission_denied", "detail": "You do not have permission to perform this action."}` | Authenticated but not allowed |
| 404 | `{"code": "not_found", "detail": "Not found."}` | Also for objects owned by someone else and for a page past the end |
| 405 | `{"code": "method_not_allowed", "detail": "Method \"DELETE\" not allowed."}` | Sent with `Allow` |
| 409 | `{"code": "protected", "detail": "This resource has dependent records and cannot be deleted."}` | Also domain conflicts raised with `ConflictError` |
| 429 | `{"code": "throttled", "detail": "Request was throttled. Expected available in 7 seconds."}` | Sent with `Retry-After` |

## Domain errors

Services reject a request that breaks a business rule by raising `core.exceptions.ApplicationError` (default `400`) or `ConflictError` (`409`). The class does not import Django Ninja; `core.api.exceptions` is the only place that turns it into HTTP, with the body that `core.errors.application_error_body` builds:

```python
from core.exceptions import ConflictError

raise ConflictError(
    "Stock is not enough.", code="insufficient_stock", extra={"available": 2}
)
```

The handler also maps Django `ValidationError` (including `full_clean()`) to `400 validation_error`, `ObjectDoesNotExist` to `404 not_found`, and `ProtectedError`/`RestrictedError` to `409 protected`. `IntegrityError` is not mapped: a constraint that can fail on user input is checked in the service and raised as an `ApplicationError`.

## Error codes

Every new code is added to this table in the same PR. Codes are never renamed once released.

| Code | HTTP status | Meaning | Raised by |
| --- | --- | --- | --- |
| `validation_error` | 400 | The body failed validation; see `fields` | Pydantic request schemas, Django `ValidationError` |
| `unknown_field` | 400 | Field-level code inside the `fields` of a `validation_error`: the body has a key that is not a field of this endpoint | Pydantic request schemas with `extra="forbid"` |
| `parse_error` | 400 | The body is not valid for its content type (for example malformed JSON, or JSON nested too deeply to decode) | `core.api.parsers.JSONParser` |
| `not_authenticated` | 401 | No valid credentials were sent (none, an unknown or expired token, or no session) | Ninja authentication (`core.api.auth`) |
| `permission_denied` | 403 | Authenticated, but not allowed, or a cookie-authenticated write without a valid CSRF token | Ninja `AuthorizationError`, Django `PermissionDenied`, Ninja's CSRF check (also `handler403`) |
| `not_found` | 404 | The resource does not exist or is not visible to the caller | `Http404`, `ObjectDoesNotExist`, pagination, unknown routes (`handler404`) |
| `method_not_allowed` | 405 | The HTTP method is not supported on this route | `core.api.negotiation.ApiContractMiddleware` |
| `not_acceptable` | 406 | The `Accept` header asks for a format the API does not produce | `core.api.negotiation.ApiContractMiddleware` |
| `unsupported_media_type` | 415 | The request `Content-Type` is not accepted | `core.api.parsers.JSONParser` |
| `throttled` | 429 | Too many requests; retry after `Retry-After` seconds | Ninja throttling |
| `idempotency_key_reused` | 409 | The `Idempotency-Key` was already used with a different payload | Create endpoints that follow [Idempotent retries](#idempotent-retries) |
| `protected` | 409 | The resource has dependent records and cannot be deleted | `ProtectedError`, `RestrictedError` |
| `request_too_large` | 413 | The body is larger than the proxy accepts (`client_max_body_size`) | nginx (`docs/NEXT-STEPS.md`) |
| `bad_request` | 400 | The request was rejected before reaching a view (for example a `Host` not in `ALLOWED_HOSTS`) | `handler400` |
| `server_error` | 500 | Unhandled error; the body never includes exception details | `handler500` |

## Error responses in OpenAPI

The schema documents error responses with the `ErrorSchema` and `ValidationErrorSchema` components (`core/api/schemas.py`):

- `400` → a `oneOf` of `ValidationErrorSchema` (field errors, `fields` present) and `ErrorSchema` (malformed JSON, `parse_error`) for `POST`, `PUT`, and `PATCH`;
- `401` and `403` → `ErrorSchema` on authenticated operations. `401` declares the `WWW-Authenticate` header;
- `404` → `ErrorSchema` for operations with a path parameter, and for page-number list endpoints (an invalid or too-deep `?page=` is 404);
- `406` → `ErrorSchema` when the operation only renders JSON;
- `415` → `ErrorSchema` on write operations that only parse JSON;
- `429` → `ErrorSchema` when throttles apply (the global ones count), with the `Retry-After` header.

`405` is not listed on every operation: the allowed methods are the operation keys themselves, and a wrong method still returns `method_not_allowed` with `Allow`.

Nothing adds them automatically: each operation spreads the error map that fits it into `response=` ([Ninja: multiple response schemas](https://django-ninja.dev/guide/responses/#multiple-response-schemas)) and passes the same map to `openapi_extra=response_docs(...)` ([Ninja: openapi_extra](https://django-ninja.dev/guide/operations/#openapi_extra)), which adds the descriptions and the headers. The maps live in `core/api/responses.py`: `READ_ERRORS` (401, 403, 406, 429), `LOOKUP_ERRORS` and `LIST_ERRORS` (plus 404), and `WRITE_ERRORS` (plus 400 and 415); combine them, for example `response={200: ThingOut, **LOOKUP_ERRORS, **WRITE_ERRORS}`. `core/tests/ninja/test_openapi_responses.py` fails when an operation leaves out the common ones. Request and response bodies are separate components: `UserUpdateIn` carries only writable fields, while `UserOut` also has the read-only `id` and `url`. The Swagger UI at `/api/v1/docs/` is staff-only and is not itself an entry in the committed contract.

Every operation sets `operation_id` explicitly, `<app>_<resource>_<action>` (for example `users_me_retrieve` under the `users` tag), so a renamed function never changes the contract ([Ninja: operation_id](https://django-ninja.dev/guide/operations/#operation_id)). When `/api/v2/` opens, serve one schema per version instead of one schema with both.

The schema is Ninja 1.8.0's own output; nothing rewrites it afterwards. Contract details come from documented options: `response=` maps (`core.api.responses`), `openapi_extra` for descriptions and headers, Pydantic `WithJsonSchema` for the path `id` pattern and `json_schema_extra` for `readOnly` and the 400 `oneOf`. An optional body (`BodyEx[T, P(default_factory=T)]`, the type-checker spelling of `Body(default_factory=...)`, [Ninja: request body](https://django-ninja.dev/guide/body/)) is a one-item `allOf` around its `$ref`, so the `api-contract` job runs oasdiff with `--flatten-allof` ([Breaking changes](#breaking-changes)).

## Throttling

Global limits are always on: `anon` per client IP (`DJANGO_THROTTLE_ANON_RATE`, default `60/min`) and `user` per authenticated user (`DJANGO_THROTTLE_USER_RATE`, default `600/min`). The defaults are a starting point, not a standard; tune them per project through the environment. A rate is `<number>/<s|m|h|d>` (`60/min`, `1000/hour`), with a count and window above zero; `config.settings.guards.assert_throttle_rates` refuses any other value at startup, in every environment, because a typo would otherwise be a 500 on the first request. A throttled request gets `429` with `Retry-After` and `code` `throttled`.

- Sensitive or expensive operations add a scope: `throttle=[AnonThrottle(), UserThrottle(), ScopedThrottle(scope="orders.checkout")]` from `core.api.throttling`, with the rate registered in `NINJA_DEFAULT_THROTTLE_RATES`. `ScopedThrottle` counts per user, or per client IP when anonymous. An unregistered scope raises `ImproperlyConfigured` when the throttle is built, that is, when the module that declares the operation is imported.
- `throttle=` on a router or operation replaces the API-level throttles instead of adding to them ([Ninja: router and operation level](https://django-ninja.dev/guide/throttling/#router-and-operation-level)), so always list `AnonThrottle()` and `UserThrottle()` with the scoped one.
- Every scoped endpoint has a test that the N+1th call returns `429` `throttled`. A test sets rates with the `throttle_rates` fixture, which patches `SimpleRateThrottle.THROTTLE_RATES` (`core/tests/ninja/adapters.py`). The test settings keep every scoped rate of `base.py` and only raise `anon` and `user` out of reach, so a scoped endpoint has its real rate under pytest.
- The client IP comes from `REMOTE_ADDR` unless a proxy is trusted. In production, `DJANGO_TRUSTED_PROXY_COUNT` (default `1`) sets `TRUSTED_PROXY_COUNT` and allauth's `ALLAUTH_TRUSTED_PROXY_COUNT`, and must equal the number of proxies that append to `X-Forwarded-For`.
- Counters live in the default cache, which is Valkey, shared by every gunicorn worker. While Valkey is unreachable they stop counting instead of failing requests ([security.md](security.md#valkey-outages)).

The client is the `X-Forwarded-For` entry that many trusted proxies from the end (`DJANGO_TRUSTED_PROXY_COUNT`, read from `ALLAUTH_TRUSTED_PROXY_COUNT` when unset). `core.net.get_client_ip` applies that rule for django-axes (`AXES_CLIENT_IP_CALLABLE`) and for Ninja throttles (`IdentMixin.get_ident` in `core/api/throttling.py`). Both read the Django setting `TRUSTED_PROXY_COUNT`. `core.net` skips blank entries, so a trailing comma (`X-Forwarded-For: 6.6.6.6,`) never yields an empty IP there; with no trusted proxy, or no address in the header, the client is `REMOTE_ADDR`. Behind nginx a trailing comma cannot happen, since `$proxy_add_x_forwarded_for` appends the real address last ([NEXT-STEPS.md](NEXT-STEPS.md)); only a request that reaches Gunicorn directly can send it, and `DOCKER_PROD_DJANGO_BIND=127.0.0.1` keeps Gunicorn off the network.

## Breaking changes

`docs/openapi.json` is the contract. The `lint` job proves it matches the code; on a pull request, the `api-contract` job runs [oasdiff](https://github.com/oasdiff/oasdiff) against the base branch's copy and fails on any change at level `ERR`: a removed path or operation, a removed or newly required request field, a removed required response field, a narrowed type or enum. oasdiff rates a removed request field only `WARN`, but request bodies reject unknown keys (`unknown_field`), so a client that still sends it gets a `400`: `.github/oasdiff-levels.txt` raises `request-property-removed` to `ERR`. Such a change goes to `/api/v2/` instead ([Deprecating endpoints and fields](#deprecating-endpoints-and-fields)). Run it locally before pushing (oasdiff 1.39.0, the version in `ci.yml`):

```bash
git show origin/main:docs/openapi.json > /tmp/openapi-base.json
oasdiff breaking /tmp/openapi-base.json docs/openapi.json --fail-on ERR --severity-levels .github/oasdiff-levels.txt --flatten-allof
oasdiff changelog /tmp/openapi-base.json docs/openapi.json --flatten-allof    # every change, breaking or not
```

The same comparison checks a change of API framework: generate the new schema, then compare it with the last committed one. Component names that Ninja emits (`UserOut`, `ErrorSchema`, `ValidationErrorSchema`, and the like) are treated as non-breaking. oasdiff reports a removed response field only when the field is in its component's `required` list, so every response component lists each property the API always renders: all model fields, including optional ones such as `User.name`, and a page's `next` and `previous`, which are `null` at either end. `Error.extra` stays optional. Request schemas set `additionalProperties: false`.

## Deprecating endpoints and fields

Mobile apps keep calling an endpoint for months after a release replaces it, so a v1 endpoint or field is deprecated before it is removed, and clients learn it two ways. This is a recipe; nothing is deprecated today.

**In the schema**, so generated clients flag it at compile time: `deprecated=True` on the route decorator ([Ninja: deprecated](https://django-ninja.dev/guide/operations/#deprecated)). Mark a removed field in the input or output schema (for example drop it from the model and document the replacement in the operation description).

**In the response**, while the endpoint is deprecated:

- `Deprecation: @<unix timestamp>` ([RFC 9745](https://www.rfc-editor.org/rfc/rfc9745)): when it was deprecated.
- `Sunset: <HTTP date>` ([RFC 8594](https://www.rfc-editor.org/rfc/rfc8594)): when it stops working, once that date is decided.
- `Link: <https://.../docs/api#...>; rel="deprecation"`: where the replacement is explained.

The operation takes Ninja's temporal `response: HttpResponse` argument and sets the headers on it ([Ninja: setting headers](https://django-ninja.dev/guide/temporal-response/#setting-headers)). This helper is a recipe, not shipped; add it as `core/api/deprecation.py` with the first deprecation:

```python
# core/api/deprecation.py
from __future__ import annotations

from datetime import UTC
from email.utils import format_datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from django.http import HttpResponse


def set_deprecation_headers(
    response: HttpResponse,
    *,
    since: datetime,
    link: str,
    sunset: datetime | None = None,
) -> None:
    """Add the Deprecation, Sunset and Link headers (RFC 9745, RFC 8594)."""
    response["Deprecation"] = f"@{int(since.timestamp())}"
    if sunset is not None:
        response["Sunset"] = format_datetime(sunset.astimezone(UTC), usegmt=True)
    response["Link"] = f'<{link}>; rel="deprecation"'
```

```python
@router.get(
    "/legacy-export/",
    response={200: ExportOut, **READ_ERRORS},
    openapi_extra=response_docs(READ_ERRORS),
    operation_id="exports_legacy_retrieve",
    deprecated=True,
)
def exports_legacy_retrieve(request: HttpRequest, response: HttpResponse) -> Export:
    set_deprecation_headers(
        response,
        since=datetime(2027, 1, 1, tzinfo=UTC),
        link="https://example.com/docs/api#legacy-export",
    )
    return export_latest()
```

Browsers expose these headers to JavaScript only when they are listed in `CORS_EXPOSE_HEADERS` (django-cors-headers), so add `["Deprecation", "Sunset", "Link"]` there with the first deprecation.

After the sunset date the endpoint answers `410 Gone` with the code `gone` (register it in [Error codes](#error-codes) when it is first used), for example `raise ApplicationError("This endpoint was removed; use /api/v1/exports/.", code="gone", status_code=410)`. An incompatible replacement goes to `/api/v2/`, following the versioning rule in AGENTS.md.

## Optimistic concurrency (opt-in)

When several clients edit the same resource, the last `PATCH` wins even when it was based on stale data (the "lost update" of [RFC 9110 §13.1](https://www.rfc-editor.org/rfc/rfc9110#section-13.1)). For resources where that matters, require a precondition:

- `GET` and `PATCH` responses carry `ETag: W/"<version>"`, derived from `updated_at` (microseconds since the epoch; it comes from the database clock) or from an integer `version` field that the service increments.
- `PATCH` and `DELETE` require `If-Match` with that value. Missing: `428 Precondition Required` with the code `precondition_required`. Different: `412 Precondition Failed` with the code `precondition_failed`. Register both in [Error codes](#error-codes) when they are first used.
- The service compares inside its `transaction.atomic()`, after `select_for_update()`, so the check and the write cannot interleave with another request.
- CORS: add `if-match` to `CORS_ALLOW_HEADERS` and `ETag` to `CORS_EXPOSE_HEADERS`.

It is not on by default: API responses are `no-store`, so there is nothing to revalidate, and Django's `ConditionalGetMiddleware` would hash each body after the view has done all the work.

```python
# apps/items/services.py
from datetime import UTC
from datetime import datetime
from datetime import timedelta

from django.db import transaction

from apps.items.models import Item
from core.exceptions import ApplicationError

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def item_etag(item: Item) -> str:
    return f'W/"{(item.updated_at - _EPOCH) // timedelta(microseconds=1)}"'


@transaction.atomic
def item_update(*, item: Item, if_match: str | None, name: str) -> Item:
    locked = Item.objects.select_for_update().get(pk=item.pk)
    if if_match is None:
        msg = "This update needs an If-Match header."
        raise ApplicationError(msg, code="precondition_required", status_code=428)
    if if_match != item_etag(locked):
        msg = "The item changed since you read it."
        raise ApplicationError(msg, code="precondition_failed", status_code=412)
    locked.name = name
    locked.full_clean()
    locked.save(update_fields=["name"])
    return locked
```

```python
# apps/items/api/views.py
ItemIdPath = PathEx[UUID, P(alias="id")]  # see Paths above


@router.get(
    "/{public_id:id}/",
    response={200: ItemOut, **LOOKUP_ERRORS},
    openapi_extra=response_docs(LOOKUP_ERRORS),
    operation_id="items_retrieve",
)
def items_retrieve(
    request: HttpRequest, response: HttpResponse, item_id: ItemIdPath
) -> Item:
    item = get_object_or_404(item_list(user=_owner(request)), public_id=item_id)
    response["ETag"] = item_etag(item)
    return item


@router.patch(
    "/{public_id:id}/",
    response={200: ItemOut, **LOOKUP_ERRORS, **WRITE_ERRORS},
    openapi_extra=response_docs({**LOOKUP_ERRORS, **WRITE_ERRORS}),
    operation_id="items_partial_update",
)
def items_partial_update(
    request: HttpRequest,
    response: HttpResponse,
    item_id: ItemIdPath,
    body: ItemUpdateIn,
) -> Item:
    item = get_object_or_404(item_list(user=_owner(request)), public_id=item_id)
    item = item_update(
        item=item, if_match=request.headers.get("If-Match"), name=body.name
    )
    response["ETag"] = item_etag(item)
    return item
```

```python
# apps/items/tests/http/test_concurrency.py
def test_a_patch_based_on_a_stale_etag_gets_412(
    user_client: Client, item: Item
) -> None:
    url = f"/api/v1/items/{item.public_id}/"
    etag = user_client.get(url).headers["ETag"]

    first = user_client.patch(
        url, {"name": "A"}, content_type="application/json", HTTP_IF_MATCH=etag
    )
    second = user_client.patch(
        url, {"name": "B"}, content_type="application/json", HTTP_IF_MATCH=etag
    )

    assert first.status_code == HTTPStatus.OK
    assert first.headers["ETag"] != etag
    assert second.status_code == HTTPStatus.PRECONDITION_FAILED
    assert second.json()["code"] == "precondition_failed"


def test_a_patch_without_if_match_gets_428(user_client: Client, item: Item) -> None:
    response = user_client.patch(
        f"/api/v1/items/{item.public_id}/",
        {"name": "A"},
        content_type="application/json",
    )

    assert response.status_code == HTTPStatus.PRECONDITION_REQUIRED
    assert response.json()["code"] == "precondition_required"
```

## Errors outside API views

Requests that never reach an API view (unknown routes, a disallowed `Host`, unhandled exceptions) are answered by the JSON `handler400`, `handler403`, `handler404`, and `handler500` in `core/errors.py`, with the same body. On `/api/` and `/_allauth/`, `core.middleware.ApiDebugErrorMiddleware` keeps that JSON body even when `DEBUG=True`, where Django would otherwise serve an HTML technical page. CSRF failures on session-authenticated pages (admin, Swagger) use Django's `CSRF_FAILURE_VIEW`, which is HTML and out of scope. Unauthenticated or non-staff calls to `/api/v1/docs/` also use the JSON envelope.

## Out of scope: `/_allauth/`

Account endpoints under `/_allauth/` (signup, login, verification, password reset) keep allauth's own format, `{"status": 400, "errors": [{"code": "...", "message": "...", "param": "..."}]}`. Clients handle the two formats separately, and must not expect the `/api/v1/` rules there:

- Some allauth errors are a bare `{"status": 429}` or `{"status": 409}` with no `errors` array (password-reset rate limits, a resend with no pending flow).
- A malformed body, or JSON nested too deeply to decode, answers a bare `400` with an empty body. That is allauth's answer to malformed JSON; `core.middleware.AllauthJsonDepthMiddleware` gives the same one for the nesting, which Python's decoder raises as `RecursionError`. With `DEBUG=True`, `ApiDebugErrorMiddleware` turns it into the `/api/v1/` envelope.
- allauth sends no `Retry-After` header on its 429 responses, and the template adds none. Clients back off on their own.
- Failed-login rate limits are a `400` with the code `too_many_login_attempts`, not a `429`. That is allauth's contract; do not turn it into a 429.
- A `X-Session-Token` that allauth no longer knows (expired, logged out, or from another server) answers `410 Gone` on authentication endpoints. The client must drop the token and start again without it.
- `methods[].at` in session payloads is a Unix float timestamp from allauth, not UTC with `Z`. `/api/v1/` datetimes stay ISO 8601 with a `Z` suffix.
- `/_allauth/` paths have no trailing slash (the opposite of `/api/v1/`). A slash is a 404 in the `/api/v1/` envelope.
- `allauth.mfa` is installed but offers no authenticator type, so `account/authenticators/totp` and `account/authenticators/recovery-codes` are not mounted (404). `account/authenticators` lists nothing, and `auth/2fa/authenticate` and `auth/2fa/reauthenticate` answer 401 or 400, never 500. Turning MFA on is [auth/mfa.md](auth/mfa.md).
- allauth also mounts its phone routes. Phone support is off: `POST /_allauth/app/v1/account/phone` answers 400 `phone_not_supported`, `GET` lists no phone, any other method answers 401 without a session and 405 (`Allow: GET, POST`) with one, and `auth/phone/verify` and its `resend` answer 409 (no pending flow). None answers 500. Turning phone numbers on is [auth/phone.md](auth/phone.md).
