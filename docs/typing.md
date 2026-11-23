# Typing

Two type checkers run on every change, and neither replaces the other. pyright is the primary checker, the one editors run. mypy runs with the django-stubs plugin, which knows the app registry, the ORM and the settings. Both are pinned in the `dev` group of `pyproject.toml` and run in `scripts/check.sh lint` ([development.md#checks](development.md#checks)), in the container and in CI ([ci.md#lint](ci.md#lint)).

## Running them

Inside the dev container, run `scripts/check.sh lint`. On the host, or from CI, use the project environment through uv:

```bash
uv run scripts/check.sh lint                               # every check below, as CI runs it
uv run pyright                                             # pyright alone, on the whole repository
uv run mypy .                                              # mypy with the django-stubs plugin
```

- `pyright` is installed as `pyright[nodejs]`. The `nodejs` extra pulls `nodejs-wheel-binaries`, pinned in `uv.lock`, and pyright-python uses it before anything else, so Node.js is never downloaded at run time. `[tool.pyright]` in `pyproject.toml` sets `standard` mode and no `include` or `exclude`. pyright's defaults already skip hidden directories (`.venv`, `.agents`), `__pycache__` and `node_modules`. The API layer runs in `strict` mode ([below](#strict-api)).
- `mypy` reads `[tool.mypy]`. Every module needs annotations (`disallow_untyped_defs`), and `enable_error_code` adds `possibly-undefined`, `truthy-bool`, `ignore-without-code`, `exhaustive-match`, `unimported-reveal` and `explicit-override` ([Overrides](#overrides)). Services, selectors, `core.exceptions` and `core.errors` also get the rest of `--strict` ([ARCHITECTURE.md#strict-typing](ARCHITECTURE.md#strict-typing)).
- ruff's `ANN` rules fail on a public function without annotations, also in pre-commit, before any checker runs.

## What each one catches

What each tool reports for a typical mistake:

| Mistake | ruff | mypy + plugin | pyright | tests |
| --- | --- | --- | --- | --- |
| A view or public function without annotations | yes (`ANN`) | yes | no | yes |
| A wrong attribute on a schema, a model field or a service argument | no | yes | yes | yes |
| A selector that returns `.values()` where a `QuerySet[Model]` is declared | no | yes | yes | yes |
| `.first().name` without a `None` check | no | yes | yes | yes |
| A method of the wrong type on a model field (`obj.public_id.upper()`) | no | yes | yes, with the field annotation ([below](#model-field-annotations)) | yes |
| A variable that may be unbound | no | yes (`possibly-undefined`) | yes | no |
| A value of unknown type in an endpoint or schema, such as a `dict` built from `Any` | no | no | yes, in `apps/*/api/` ([strict](#strict-api)) | no |
| A hook that a dependency upgrade renamed or removed, so the method overrides nothing | no | yes ([`@override`](#overrides)) | yes | only if a test calls it |
| A setting that does not exist (`settings.API_MAX_PAGE_NUMBR`) | no | yes (plugin) | no | yes |
| An ORM lookup on a field that does not exist (`filter(nmae=...)`) | no | yes (plugin) | no | yes |
| `user.pk` used as something other than an `int` | no | yes | no (`Any`) | yes |
| An operation that returns a status code missing from its `response=` map | no | no | no | yes |

Only mypy with the plugin sees the settings and the ORM. Only the tests see what Ninja does at run time.

## Strict API

pyright checks `apps/*/api/` and `core/api/schemas.py` in `strict` mode (`strict` in `[tool.pyright]`). Every endpoint and schema an app adds lives under `apps/<app>/api/`, so new API code gets it without any change. In strict mode an `Unknown` type is an error: a value whose type pyright cannot work out, such as a `dict` taken from an `Any`, or a generic class used without its type arguments.

The raw input of a Pydantic validator is `Any`, so it needs a real type before you use it:

- After `isinstance(data, dict)`, `cast("dict[str, object]", data)`: a JSON object's keys are always strings.
- Annotate what you read from a context: `context: dict[str, Any] = info.context or {}`, then `request: HttpRequest | None = context.get("request")`.

The rest of `core/api/` stays in `standard` mode. Strict would demand stubs for django-allauth, which ships no types (`reportMissingTypeStubs`), and it would reject Django Ninja's own partially unknown types. Neither has a fix in this repository that is not a local stub or a suppression.

## Overrides

Every method that overrides a method or attribute of a base class says so with `@override`, from `typing`: model `save` and `clean`, admin and allauth adapter hooks, the cache backend, throttles, paginators, parsers, middleware and management commands. Both checkers enforce it: pyright with `reportImplicitOverride`, mypy with the `explicit-override` error code.

When a dependency upgrade renames or removes a hook the template overrides, the method keeps its `@override` but no longer overrides anything. Both checkers then fail ("marked as override, but no base method"), where before the method would silently never be called.

- Put `@override` above the method's other decorators. With `@property`, put it below `@property`: `override` takes a function, not a `property`.
- ruff does not ask for keyword-only booleans (`FBT001`) in a method marked `@override`: its signature belongs to the base class.

## Suppressions

Each checker has its own suppression comment, scoped to one rule:

- pyright reads only `# pyright: ignore[rule]` (`enableTypeIgnoreComments = false`). It fails on a suppression that is no longer needed (`reportUnnecessaryTypeIgnoreComment`).
- mypy reads only `# type: ignore[code]`, and fails on an unused one (`warn_unused_ignores`).
- A line that both checkers flag carries both comments.

Before you add either comment, try a real type:

- narrow with `isinstance`;
- annotate the variable;
- declare the reverse accessor (`emailaddress_set: RelatedManager[EmailAddress]`, from `django_stubs_ext.db.models.manager`);
- use `cast()` for a library that returns `Any`.
- in a test, build the real object instead of passing `None` or a `SimpleNamespace`: an unsaved model instance, a `FileField` on no model, a hand-built `UploadedFile`. A parsed JSON body is `dict[str, Any]`, and an attribute a logging filter adds is read through a `logging.Formatter`.

A suppression that stays is listed in [Upstream workarounds](#upstream-workarounds).

## Model field annotations

Every model field carries its django-stubs generic type:

```python
name: CharField[str | int | Combinable, str] = CharField(max_length=255)
owner: ForeignKey[User | Combinable, User] = ForeignKey(User, on_delete=PROTECT)
```

Without the annotation pyright sees the field as `Unknown` and lets any use of it pass.

- The two type arguments are the set and get types, the `_pyi_private_set_type` and `_pyi_private_get_type` of the field class in django-stubs.
- A nullable field adds `| None` to both. mypy's plugin fails when the annotation and `null=` disagree, so pyright trusts the annotation and mypy checks it.
- `core/tests/test_model_field_annotations.py` fails on an unannotated field in `apps/*/models.py` or `core/models.py`.
- A field that redeclares an attribute a base class already annotates keeps the base's declared type, because pyright treats a mutable class variable as invariant. `User.is_active` is `bool | BooleanField[bool | Combinable, bool]`, as django-stubs 6.1.2 annotates `AbstractBaseUser.is_active`.
- With `from __future__ import annotations` the annotation has no effect at run time, and `makemigrations` ignores it.

This is a workaround with an expiry date; see [Tracked: model field annotations](#tracked-model-field-annotations).

## Upstream workarounds

These are the places where the code works around a limitation of django-stubs, pyright, Django Ninja or another dependency. The ones with an upstream issue or pull request to wait for are in [Tracked upstream](#tracked-upstream): one table, a command to check each one, and what to remove when the fix ships. The rest are permanent: each comes with its reason and its source. Check this section whenever django-stubs, Django Ninja or pyright is upgraded ([maintain-django-template](../.agents/skills/maintain-django-template/SKILL.md)).

### Tracked upstream

Run each row's check on every django-stubs or django-ninja upgrade ([maintain-django-template](../.agents/skills/maintain-django-template/SKILL.md), step 6). A merged pull request or a closed issue is not enough: the fix counts once it is in a release the template pins.

| Workaround | Where | Waits for | Check | When it ships |
| --- | --- | --- | --- | --- |
| Explicit generic annotations on every model field, and `core/tests/test_model_field_annotations.py` | `apps/*/models.py`, `core/models.py` | [typeddjango/django-stubs#3763](https://github.com/typeddjango/django-stubs/pull/3763): merged on 2026-11-19, in no release yet | `gh pr view 3763 -R typeddjango/django-stubs --json state,mergedAt`, then the release notes (`gh release list -R typeddjango/django-stubs --limit 3`) | Follow [Tracked: model field annotations](#tracked-model-field-annotations). |
| `core.api.auth.cookieAuth` overrides `__call__`, not `authenticate`, so a session cookie does not turn an `X-Session-Token` request into a CSRF 403 ([Django Ninja mechanisms](#django-ninja-mechanisms)) | `core/api/auth.py` | [vitalik/django-ninja#1464](https://github.com/vitalik/django-ninja/issues/1464), fixed by the open pull request [#1852](https://github.com/vitalik/django-ninja/pull/1852) | `gh pr view 1852 -R vitalik/django-ninja --json state,mergedAt`, then the release notes (`gh release list -R vitalik/django-ninja --limit 3`) | Bump django-ninja, override `authenticate` as the [Ninja guide](https://django-ninja.dev/guide/authentication/#multiple-authenticators) shows, keep `core/tests/http/test_auth_csrf_1464.py` passing, and drop the row in [Django Ninja mechanisms](#django-ninja-mechanisms). |
| `# pyright: ignore[reportArgumentType]` on `Collector.collect([...])`. django-stubs types `objs` as the protocol `_IndexableCollection`, whose `__getitem__` also takes `index` by name; typeshed's `list` takes it positional-only, so pyright 1.1.414 rejects the list (mypy accepts it). `Collector` itself is undocumented Django API ([user-deletion/hard-delete.md](user-deletion/hard-delete.md)). | `apps/users/management/commands/hard_delete_user.py`, `apps/users/tests/test_signals.py` | [typeddjango/django-stubs#3849](https://github.com/typeddjango/django-stubs/issues/3849): a positional-only `index` in the protocol, which lets pyright accept the list (probed with a copy of the stub) | `gh issue view 3849 -R typeddjango/django-stubs --json state,closedAt` | Bump django-stubs and delete both comments. `scripts/check.sh lint` fails on a stale one (`reportUnnecessaryTypeIgnoreComment`). |
| Both comments on `Input = PageNumberIn` and `Output = PageNumberOut`. Ninja declares `Input` and `Output` as nested classes, so both checkers reject swapping in unrelated schemas, as Ninja's own `CursorPagination` does. The alias keeps the component name Ninja publishes from `Input`, `PageNumberIn`; a nested `class Input(PageNumberIn)` still fails pyright (`reportIncompatibleVariableOverride`) and publishes `Input` (probed with django-ninja 1.8.0 and pyright 1.1.414). | `core/api/pagination.py` | [vitalik/django-ninja#1873](https://github.com/vitalik/django-ninja/issues/1873). A `ClassVar[type[Schema]]` on `PaginationBase` alone would not fix it: `PageNumberPagination` redeclares `Input` as a nested class, which narrows the type again (probed). | `gh issue view 1873 -R vitalik/django-ninja --json state,closedAt` | Bump django-ninja and delete the comments once both checkers accept the aliases. `docs/openapi.json` must not change: the component stays `PageNumberIn`. |

### Tracked: model field annotations

- **What:** the explicit generic annotation on every model field ([Model field annotations](#model-field-annotations)) and its test.
- **Why:** django-stubs 6.1.2 types fields as `Field[_ST, _GT]` with no TypeVar defaults. Only the mypy plugin resolves them, so pyright sees `Unknown`. The django-stubs README calls pyright support "basic".
- **Upstream:** [typeddjango/django-stubs#3763](https://github.com/typeddjango/django-stubs/pull/3763), "Resolve field nullability at the call site with `__init__` overloads". It removes `_pyi_private_set_type` and `_pyi_private_get_type`, and gives every field PEP 696 defaults that any checker can read. It replaces [#3317](https://github.com/typeddjango/django-stubs/pull/3317). The pull request warns that a bare annotation such as `x: models.IntegerField = models.IntegerField(null=True)` will be rejected; annotations with explicit type arguments, as here, stay valid.
- **When it ships:** check each django-stubs release for #3763. Once it is released:
  1. Bump django-stubs.
  2. Delete the field annotations and `core/tests/test_model_field_annotations.py`.
  3. Confirm that `uv run pyright` still reports `obj.public_id.upper()` in a scratch file.

### Permanent

| Workaround | Where | Reason | Source |
| --- | --- | --- | --- |
| `# pyright: ignore[reportIncompatibleVariableOverride]` on a factory's nested `class Meta` | `apps/users/tests/factories.py` | A nested `Meta` is a new class that replaces the base class's `Meta`, and pyright reports that by design in `standard` mode. A model avoids it by subclassing its parent's `Meta`, as Django documents: `User.Meta` extends `AbstractBaseUser.Meta` and `PermissionsMixin.Meta`, and a domain model writes `class Meta(BaseModel.Meta):`. A factory cannot: factory-boy's metaclass consumes `Meta`, so `DjangoModelFactory.Meta` does not exist at run time. | [microsoft/pyright#6814](https://github.com/microsoft/pyright/issues/6814) (as designed: "This behavior is correct, not a bug"); [typeddjango/django-stubs#748](https://github.com/typeddjango/django-stubs/issues/748); [Meta inheritance](https://docs.djangoproject.com/en/6.1/topics/db/models/#meta-inheritance) |
| Both comments on the `time_cost`, `memory_cost` and `parallelism` properties | `core/hashers.py` | Properties make `override_settings` reach the hasher. django-stubs declares them as plain `int` attributes. | django-stubs 6.1.2 `contrib/auth/hashers.pyi` |
| Both comments on `before_send` and `before_send_transaction` | `core/sentry.py` | sentry-sdk types an event as a `TypedDict`. `scrub_event` walks arbitrary nested data as `dict[str, Any]`. | sentry-sdk 2.75.0 `sentry_sdk/_types.py` (`Event`, `EventProcessor`) |
| `# type: ignore[misc]` on a factory's `__new__` under `TYPE_CHECKING` | `apps/*/tests/factories.py` | Calling a factory class returns a model instance. factory-boy 3.3.3 types `create()` and `build()` as returning the model, but not the metaclass `__call__` behind `UserFactory(...)`, so this `__new__` tells the checkers; mypy rejects a `__new__` that returns another type. | factory-boy 3.3.3 `factory/base.py` (`FactoryMetaClass.__call__`, `BaseFactory.create`) |

### Django Ninja mechanisms

Django Ninja mechanisms that differ from the example in the Ninja guide, each for a documented reason:

| Mechanism | Ninja guide | What the template does and why |
| --- | --- | --- |
| Session authentication | [Multiple authenticators](https://django-ninja.dev/guide/authentication/#multiple-authenticators), [CSRF](https://django-ninja.dev/guide/csrf/#session-django_auth-authentication) | `core.api.auth.cookieAuth` overrides `__call__`, not `authenticate`. Ninja checks CSRF before `authenticate` runs, so a request with a session cookie and an `X-Session-Token` would get a CSRF 403 instead of trying the token. The bug is [vitalik/django-ninja#1464](https://github.com/vitalik/django-ninja/issues/1464); the open fix, [#1852](https://github.com/vitalik/django-ninja/pull/1852), is not in 1.8.0. |
| `HEAD`, `OPTIONS`, `405` | [Handling multiple methods](https://django-ninja.dev/guide/operations/#handling-multiple-methods) | `core.api.negotiation.ApiContractMiddleware` answers them for every route, so operations declare their real method only. There are no duplicate `operationId`s, and `Allow` comes from the registered operations ([api.md#methods-and-status-codes](api.md#methods-and-status-codes)). |
| Throttles | [Custom throttles](https://django-ninja.dev/guide/throttling/#custom-throttles) | Ninja keeps one throttle instance per process, so `core.api.throttling` checks each request on a per-thread copy. This relies on Ninja calling `wait()` right after `allow_request()` in the same thread (`ninja/operation.py` in 1.8.0). Ninja does not document that order. `core/tests/ninja/test_throttle_call_order.py` pins it; recheck it on every Ninja upgrade. |
| Page count | [Custom pagination classes](https://django-ninja.dev/guide/pagination/#custom-pagination-classes) | The guide's example calls the private `_items_count`; `core.api.pagination` defines its own `count_items`. |
| Schema and Swagger views | [Protecting the docs](https://django-ninja.dev/guide/openapi/#protecting-the-docs) | `openapi_url=None` and `docs_url=None`, with project views. This keeps YAML by default, the URL names, and staff access with `X-Session-Token`. |
| Parser errors | [Request parsers](https://django-ninja.dev/guide/parsers/) | Ninja wraps a parser error as `HttpError(400, "Cannot parse request body")`. `core.api.exceptions` reads `__cause__` to keep the parser's own `400` or `415` detail. Ninja does not document that chaining; a test pins it. |
| Optional body | [Request body](https://django-ninja.dev/guide/body/#singular-values-in-the-body) | `BodyEx[T, P(default_factory=T)]` instead of `Body[T, P(...)]`: same object at run time, but pyright accepts it. Ninja wraps the `$ref` in a one-item `allOf`, so CI runs oasdiff with `--flatten-allof` ([api.md#breaking-changes](api.md#breaking-changes)). |
| Path before body | — | A path validator runs before the body is parsed, so an unknown id is `404` before a malformed body is `400`. That order is Ninja's own and not documented; `core/tests/ninja/test_contract_regressions.py` pins it. |
| Test client | [Testing: JSON body](https://django-ninja.dev/guide/testing/#json-body) | `ninja.testing.TestClient` needs `content_type="application/json"` next to `json=` in 1.8.0, unlike the guide, because `core.api.parsers` checks the content type. It only affects `tests/ninja/`. |
