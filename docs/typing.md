# Typing

Two type checkers run on every change, and neither replaces the other. pyright is the primary checker, the one editors run. mypy runs with the django-stubs plugin, which knows the app registry, the ORM and the settings. Both are pinned in the `dev` group of `pyproject.toml` and run in `scripts/check.sh lint` ([development.md#checks](development.md#checks)), in the container and in CI ([ci.md#lint](ci.md#lint)).

## Running them

Inside the dev container, run `scripts/check.sh lint`. On the host, or from CI, use the project environment through uv:

```bash
uv run scripts/check.sh lint                               # every check below, as CI runs it
uv run pyright                                             # pyright alone, on the whole repository
uv run mypy .                                              # mypy with the django-stubs plugin
MYPYPATH=typings uv run stubtest environ --ignore-missing-stub   # typings/ against the installed package
```

- `pyright` is installed as `pyright[nodejs]`. The `nodejs` extra pulls `nodejs-wheel-binaries`, pinned in `uv.lock`, and pyright-python uses it before anything else, so Node.js is never downloaded at run time. `[tool.pyright]` in `pyproject.toml` sets `standard` mode and no `include` or `exclude`. pyright's defaults already skip hidden directories (`.venv`, `.agents`), `__pycache__` and `node_modules`.
- `mypy` reads `[tool.mypy]`. Every module needs annotations (`disallow_untyped_defs`), and `enable_error_code` adds `possibly-undefined`, `truthy-bool`, `ignore-without-code`, `exhaustive-match` and `unimported-reveal`. Services, selectors, `core.exceptions` and `core.errors` also get the rest of `--strict` ([ARCHITECTURE.md#strict-typing](ARCHITECTURE.md#strict-typing)).
- `stubtest` compares the stubs in `typings/` with the installed package, so a dependency bump that changes a signature fails `lint`.
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
| A setting that does not exist (`settings.API_MAX_PAGE_NUMBR`) | no | yes (plugin) | no | yes |
| An ORM lookup on a field that does not exist (`filter(nmae=...)`) | no | yes (plugin) | no | yes |
| `user.pk` used as something other than an `int` | no | yes | no (`Any`) | yes |
| An operation that returns a status code missing from its `response=` map | no | no | no | yes |

Only mypy with the plugin sees the settings and the ORM. Only the tests see what Ninja does at run time.

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
- With `from __future__ import annotations` the annotation has no effect at run time, and `makemigrations` ignores it.

This is a workaround with an expiry date; see [Tracked: model field annotations](#tracked-model-field-annotations).

## Local stubs

`typings/` holds stubs for packages that ship no types. Today that is only django-environ 0.14.0, which has no `py.typed` marker. pyright reads the stubs through `stubPath` and mypy through `mypy_path`. `stubtest` keeps them honest.

## Upstream workarounds

These are the places where the code works around a limitation of django-stubs, pyright, Django Ninja or another dependency. Only the first one has an upstream fix to wait for. The rest are permanent: each comes with its reason and its source. Check this section whenever django-stubs, Django Ninja or pyright is upgraded ([maintain-django-template](../.agents/skills/maintain-django-template/SKILL.md)).

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
| `# pyright: ignore[reportIncompatibleVariableOverride]` on a factory's nested `class Meta` | `apps/users/tests/factories.py` | A nested `Meta` is a new class that replaces the base class's `Meta`, and pyright reports that by design in `standard` mode. A model avoids it by subclassing its parent's `Meta`, as Django documents: `User.Meta` extends `AbstractUser.Meta`, and a domain model writes `class Meta(BaseModel.Meta):`. A factory cannot: factory-boy's metaclass consumes `Meta`, so `DjangoModelFactory.Meta` does not exist at run time. | [microsoft/pyright#6814](https://github.com/microsoft/pyright/issues/6814) (as designed: "This behavior is correct, not a bug"); [typeddjango/django-stubs#748](https://github.com/typeddjango/django-stubs/issues/748); [Meta inheritance](https://docs.djangoproject.com/en/6.1/topics/db/models/#meta-inheritance) |
| `# pyright: ignore[reportIncompatibleVariableOverride]` on `objects: ClassVar[UserManager]` | `apps/users/models.py` | django-stubs declares `AbstractUser.objects: ClassVar[UserManager[Self]]`, and the custom manager is not that exact type for pyright. | django-stubs 6.1.2 `contrib/auth/models.pyi`; [pyright `reportIncompatibleVariableOverride`](https://github.com/microsoft/pyright/blob/main/docs/configuration.md#reportIncompatibleVariableOverride) |
| `# type: ignore[override]` and `# pyright: ignore[reportIncompatibleMethodOverride]` on `create_user` and `create_superuser` | `apps/users/managers.py` | An email-login manager drops the `username` parameter, as Django's guide for a custom user model does. The stubs keep Django's signature. | [Writing a manager for a custom user model](https://docs.djangoproject.com/en/6.1/topics/auth/customizing/#writing-a-manager-for-a-custom-user-model); django-stubs 6.1.2 `contrib/auth/models.pyi` |
| `first_name`, `last_name`, `username = None` with both comments | `apps/users/models.py` | Django removes an inherited field with `None`, and both checkers see a `None` assigned to a field. | [Abstract base classes](https://docs.djangoproject.com/en/6.1/topics/db/models/#abstract-base-classes) ("removed with None") |
| Both comments on the `time_cost`, `memory_cost` and `parallelism` properties | `core/hashers.py` | Properties make `override_settings` reach the hasher. django-stubs declares them as plain `int` attributes. | django-stubs 6.1.2 `contrib/auth/hashers.pyi` |
| `# pyright: ignore[reportArgumentType]` on `Collector.collect([...])` | `apps/users/management/commands/hard_delete_user.py`, `apps/users/tests/test_signals.py` | django-stubs types `objs` as the protocol `_IndexableCollection`, whose `__getitem__` also takes `index` by name; typeshed's `list` and `tuple` take it positional-only, so pyright 1.1.414 accepts neither. | django-stubs 6.1.2 `db/models/deletion.pyi` and `utils/datastructures.pyi`; typeshed `stdlib/builtins.pyi` |
| Both comments on `before_send` and `before_send_transaction` | `core/sentry.py` | sentry-sdk types an event as a `TypedDict`. `scrub_event` walks arbitrary nested data as `dict[str, Any]`. | sentry-sdk 2.75.0 `sentry_sdk/_types.py` (`Event`, `EventProcessor`) |
| Both comments on `Input = PageNumberIn` and `Output = PageNumberOut` | `core/api/pagination.py` | Ninja declares `Input` and `Output` as nested classes. An alias keeps the published component names. | django-ninja 1.8.0 `ninja/pagination.py`; [Custom pagination classes](https://django-ninja.dev/guide/pagination/#custom-pagination-classes) |
| `# type: ignore[misc]` on a factory's `__new__` under `TYPE_CHECKING` | `apps/*/tests/factories.py` | factory-boy returns a model instance from the factory class, which mypy rejects as a `__new__` return type. | factory-boy `DjangoModelFactory` |
| Both comments on calls with deliberately invalid input | `core/tests/test_files.py`, `core/tests/test_storage_settings.py`, `core/tests/test_logging.py`, `core/tests/test_decisions.py`, `apps/users/tests/http/test_emails.py` | The test passes `None` or reads an untyped payload on purpose. | — |
| `typings/environ/` stubs | `typings/` | django-environ 0.14.0 ships no types. `stubtest` checks the stubs against each release. | PyPI wheel `django_environ-0.14.0` (no `py.typed`) |

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
