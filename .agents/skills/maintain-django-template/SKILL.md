---
name: maintain-django-template
description: Audit this repository for outdated Python dependencies, Docker images, pre-commit hooks, GitHub Actions, hand-pinned tools and end-of-life dates, then propose a tiered upgrade plan (patch-safe, minor, major). Read-only until the user approves the plan; after that, one commit per upgrade with scripts/check.sh and the Bruno smoke green. Use when the repository has not been updated for a while, or when the user asks what can be updated, to bump dependencies or minimum versions, to refresh images, hooks or actions, or to check end-of-life dates.
---

# Maintain the template

Dependabot already proposes part of this every week ([docs/ci.md](../../../docs/ci.md#dependabot)). This skill covers what it skips: the hand-pinned tools ([Tool pins](../../../docs/ci.md#tool-pins)), the service images in `ci.yml` ([Image pins](../../../docs/ci.md#image-pins)), Python minor releases, PostgreSQL and Valkey majors, and end-of-life dates. It also covers the gap when Dependabot is paused: GitHub pauses it after 90 days with an open Dependabot pull request that nobody touched.

Mandatory rules stay in [AGENTS.md](../../../AGENTS.md); the reasons behind each pin are in [docs/ci.md](../../../docs/ci.md). Do not copy them here.

## When to run

Run when the user asks what can be updated, or before new work on a tree whose last commit is months old. If `pyproject.toml` still says `name = "project"` and the user is starting a product, run [personalize-django-template](../personalize-django-template/SKILL.md) first.

## Rules

- **Read-only until approved.** The audit changes no file in the repository. Scratch files go in a `mktemp -d` directory. Do not run `uv lock` without `--dry-run`, `uv sync`, or `pre-commit autoupdate` on the repository's own config. Only after the user approves the plan do you edit, one commit per upgrade.
- **Primary sources, read now.** Every "latest" version comes from the registry, index or release page in this session. Read changelogs, upgrade notes and deprecations with the Context7 MCP server (`resolve-library-id`, then `query-docs` for the target version) when it is available, or from the project's release notes. Cite what you read. Never rely on memory for a version or a breaking change.
- **Never downgrade.** `pre-commit autoupdate` takes the newest tag on the hook repository's default branch, which can be older than the current pin. Compare every proposal with the project's latest release.
- **Cooldown.** The 7-day wait applies only to Dependabot (`cooldown.default-days: 7` in `.github/dependabot.yml`, and [docs/ci.md](../../../docs/ci.md#dependabot)). Manual upgrades of this template may pin the latest stable release without waiting. A security fix skips the Dependabot wait.
- **Nothing gets weaker.** `audit` stays a required check, and an advisory is ignored only by its ID with a reason and an expiry date ([docs/ci.md](../../../docs/ci.md#audit)). Never remove a secret, `.env`, key or credentials pattern from `.gitignore`, `.dockerignore` or `scripts/package-template.sh`. Do not lower the coverage floor, drop `filterwarnings = ["error"]` or pytest `strict`. Bruno requests keep `bru.setVar`.
- **Pins move together.** Each group below has a test or a doc that keeps its copies equal. Bump the whole group in one commit.
- **Decisions hold.** Read [docs/decisions/](../../../docs/decisions/README.md) before planning. An upgrade that would undo a recorded decision (a required check, the error body, the test layout, the import contracts) names the record in the plan. If its reason no longer holds, propose a superseding record; never drop the rule quietly.

## 1. Starting point

```bash
git status --short                   # must print nothing
git log -1 --format='%h %cs %s'
gh pr list --app dependabot --state open
gh run list --workflow audit.yml --limit 3
```

Open Dependabot pull requests are the first candidates: list them in the plan instead of redoing them. One open for more than 90 days (GitHub then pauses Dependabot and shows a banner on its pull requests) or a red `audit` run goes at the top of the report. Without `gh`, ask the user or read the repository page.

Set up the audit, with the same uv and pip-audit versions as CI:

```bash
scratch="$(mktemp -d)"
uv_pin="$(sed -n 's/^ *version: "\([0-9.]*\)"$/\1/p' .github/actions/setup-python-env/action.yml)"
ci_uv() { uvx "uv@${uv_pin}" "$@"; }    # CI's uv; a function also works in zsh
pip_audit="$(rg -o --no-filename 'pip-audit==[0-9.]+' .github/workflows/audit.yml)"
```

## 2. Audit (read-only)

### Python packages

Every dependency is pinned with `==`, so check both levels:

```bash
ci_uv lock --upgrade --dry-run                           # transitive packages the lock would move
ci_uv tree --outdated --depth 1 --all-groups --locked    # direct pins, marked "(latest: …)"
ci_uv export --locked --no-emit-project --all-extras --format requirements.txt --quiet -o "$scratch/requirements.txt"
uvx "$pip_audit" --strict --disable-pip -r "$scratch/requirements.txt"
```

The pins in `pyproject.toml` are also the minimum versions. For each direct package that moves, note:

- `requires-python`, the Python classifier, ruff `target-version`, mypy `python_version` and pyright `pythonVersion` for a Python minor release.
- django-upgrade `--target-version` (`.pre-commit-config.yaml` and `scripts/check.sh`), `django-stubs`, and every mention of the Django version for a Django feature release: the "Django X.Y" text and the `docs.djangoproject.com/en/X.Y/` links (`rg -n 'Django 6\.1|/en/6\.1/' --glob '!uv.lock' .` for 6.1).
- Which Django versions the new django-allauth, django-ninja, django-axes and django-cors-headers support, and the reverse.
- `django-stubs` and `django-stubs-ext` move together and with Django; `celery-types` with Celery, `kombu` with Celery, `pydantic` within the range django-ninja supports, and `pydantic-settings` with `pydantic`; `python-dotenv` stays within the range pydantic-settings requires, and `dj-database-url` moves on its own. `hypothesis` stays within the range `schemathesis` requires; a new release of either can generate new requests in `core/tests/test_openapi_conformance.py`, and what they find is a bug to fix, not noise. Pyright (`pyright[nodejs]`) moves on its own, and a new release may report new errors: fix them, or scope a suppression and record it in [docs/typing.md](../../../docs/typing.md#suppressions).
- When django-stubs, django-ninja or pyright moves, review [docs/typing.md#upstream-workarounds](../../../docs/typing.md#upstream-workarounds) (step 6 below).

### Docker images

```bash
rg -n --no-heading -e '^FROM ' -e 'COPY --from=\S+:' -e 'image: \S+@sha256' docker .github
```

For each image, find the newest tag on the same line and the next major. Docker Hub (`library/` for official images):

```bash
curl -fsSL "https://hub.docker.com/v2/namespaces/library/repositories/postgres/tags?page_size=100&name=18." \
  | jq -r '.results[].name' | rg '^18\.\d+-alpine3\.\d+$' | sort -V | tail -3
```

A tag keeps its name when the image is rebuilt with OS fixes, so also compare the pinned digest with the tag's current one (`docker buildx imagetools inspect <image>:<tag>`, or without Docker):

```bash
digest() {  # digest <registry: docker.io or ghcr.io> <repository> <tag>
  local token host
  if [ "$1" = docker.io ]; then
    host=registry-1.docker.io
    token="$(curl -fsSL "https://auth.docker.io/token?service=registry.docker.io&scope=repository:$2:pull" | jq -r .token)"
  else
    host=ghcr.io
    token="$(curl -fsSL "https://ghcr.io/token?scope=repository:$2:pull" | jq -r .token)"
  fi
  curl -fsSI -H "Authorization: Bearer $token" \
    -H 'Accept: application/vnd.oci.image.index.v1+json' \
    -H 'Accept: application/vnd.docker.distribution.manifest.list.v2+json' \
    "https://$host/v2/$2/manifests/$3" | tr -d '\r' | awk 'tolower($1)=="docker-content-digest:"{print $2}'
}
digest docker.io library/python 3.14.10-slim    # e.g. the Python base image
digest ghcr.io astral-sh/uv 0.13.9               # e.g. the uv image
```

- `python`: Dependabot proposes patch releases only. A minor release is a Python upgrade (see "Python minor release" below).
- `postgres` and `valkey/valkey`: the same tag and digest in both compose files and every service in `ci.yml`; `core/tests/test_compose.py` checks it. A major goes in its own tier with a migration note: PostgreSQL needs a dump and restore, and a new Valkey can write data files the old one refuses ([docs/ci.md](../../../docs/ci.md#dependabot)).
- `ghcr.io/astral-sh/uv`: part of the uv group below.

### Tools pinned by hand

| Tool | Where it is pinned (all of them move together) |
| --- | --- |
| uv | `.github/actions/setup-python-env/action.yml`, `COPY --from` in both Dockerfiles (tag and digest), the `uv-pre-commit` hook |
| gitleaks | the pre-commit hook, `GITLEAKS_VERSION` and `GITLEAKS_SHA256` in `ci.yml` |
| oasdiff | `OASDIFF_VERSION` and `OASDIFF_SHA256` in `ci.yml`, the version in `docs/api.md#breaking-changes`; check that the ids in `.github/oasdiff-levels.txt` still exist (an unknown id fails the run) |
| lychee | `lycheeVersion` in `ci.yml` and `links.yml` (with the action SHA), the comment in `lychee.toml` |
| Bruno CLI | `@usebruno/cli@` in `ci.yml` and `docs/development.md` |
| pip-audit | `audit.yml` and `docs/ci.md` |
| markdownlint-cli2 | the pre-commit hook (Dependabot bumps it) and the comment in `.markdownlint-cli2.yaml` (by hand) |

```bash
latest() { curl -fsSL "https://api.github.com/repos/$1/releases/latest" | jq -r .tag_name; }
latest astral-sh/uv; latest gitleaks/gitleaks; latest lycheeverse/lychee    # lychee tags read lychee-vX.Y.Z
latest oasdiff/oasdiff    # checksum: the release's checksums.txt, line linux_amd64.tar.gz
npm view @usebruno/cli version
curl -fsSL https://pypi.org/pypi/pip-audit/json | jq -r .info.version
```

For a gitleaks bump, take the new checksum from the release:

```bash
v=8.30.1    # the version you are moving to
curl -fsSL "https://github.com/gitleaks/gitleaks/releases/download/v${v}/gitleaks_${v}_checksums.txt" | rg 'linux_x64\.tar\.gz'
```

### pre-commit hooks

```bash
cp .pre-commit-config.yaml "$scratch/"
pre-commit autoupdate --config "$scratch/.pre-commit-config.yaml"
diff -u .pre-commit-config.yaml "$scratch/.pre-commit-config.yaml" || true    # exit 1 = proposals
```

ruff and django-upgrade also live in the `dev` group of `pyproject.toml`; `core/tests/test_compose.py` fails while the two differ. The uv and gitleaks hooks belong to their groups above. Drop any proposal older than the current pin.

### GitHub Actions

Actions are pinned by commit SHA, with the version in a comment:

```bash
rg -o --no-filename -r '$1 $2' 'uses: ([\w.-]+/[\w.-]+)@[0-9a-f]{40} # (v\S+)' .github | sort -u \
  | while read -r repo pinned; do echo "$repo $pinned -> $(latest "$repo")"; done
```

To bump one, take the SHA of the new tag (the `^{}` line, when there is one, is the commit of an annotated tag):

```bash
git ls-remote --tags https://github.com/actions/checkout 'v7.0.1' 'v7.0.1^{}'
```

### End of life

```bash
for product in python django postgresql valkey; do
  curl -fsSL "https://endoflife.date/api/v1/products/$product/" | jq -r --arg p "$product" \
    '.result.releases[:3][] | "\($p) \(.name): latest \(.latest.name // "-"), EOL \(.eolFrom // "-")\(if .isLts then " (LTS)" else "" end)"'
done
```

Flag every pinned line that reaches its end of life within 6 months, and say which release replaces it.

## 3. Report and stop

Show one table and wait for the user's approval. Write nothing until then.

| Tier | Component | From → to | Files | Notes (changelog read, EOL, migration) | Risk |
| --- | --- | --- | --- | --- | --- |

- **Tier 0, security:** a `pip-audit` finding, a Trivy finding in CI or a security release (Django, django-allauth). It goes first, whatever its size.
- **Tier 1, patch-safe:** patch releases on the same line, transitive lock updates, a newer digest of the same tag, action and tool patch releases.
- **Tier 2, minor:** minor releases whose changelog lists no backwards-incompatible change for what this template uses.
- **Tier 3, major or breaking:** majors; Django, Python and PostgreSQL feature releases; Valkey majors; anything whose changelog lists a breaking change or removes a deprecation the code still triggers. Each needs its own note: what breaks, the steps, and how to roll back.

Add the open Dependabot pull requests, and anything you could not check and why. When a tier touches a decision record, add a column note with its number (for example `0008` for the audit).

## 4. Apply (after approval)

1. Branch from an up-to-date `main`: `chore/maintenance-<yyyy-mm>`.
2. One commit per upgrade or pin group, in tier order. Conventional Commits in English: `chore(deps): bump django from 6.1.3 to 6.1.4`, `ci: bump lychee to v0.25.0`, `chore(docker): bump postgres to 18.7`.
3. Python packages: edit the `==` pin in `pyproject.toml`, then `uv lock` (not under `UV_FROZEN=1`) and `uv sync --locked` in the dev container. For a transitive package alone, `uv lock --upgrade-package <name>`.
4. Images: change tag and digest together, in every file the group lists.
5. Before each commit, `rg -n -F '<old version>' .` must find no copy left behind.
6. After each commit:

   ```bash
   scripts/check.sh                 # lint (ruff, mypy, pyright), migrations check, pytest with coverage
   python manage.py export_openapi_schema --settings config.settings.test --api config.api.api --indent 2 > docs/openapi.json
   git diff --exit-code docs/openapi.json
   ```

   After a django-stubs, django-ninja or pyright bump, walk [docs/typing.md#upstream-workarounds](../../../docs/typing.md#upstream-workarounds):

   - Every django-stubs or django-ninja bump: walk [Tracked upstream](../../../docs/typing.md#tracked-upstream). Run each row's check command; for a fix that is in the release you are moving to, follow its "When it ships" column in the same pull request. A merged pull request or a closed issue is not enough: the fix must be in that release (read its release notes). Report each row's state, even when nothing ships.
   - Every bump: remove each suppression in the "Permanent" tables one at a time and run `scripts/check.sh lint`. One that is no longer needed goes, with its row. Both checkers already fail on a stale suppression (mypy `warn_unused_ignores`, pyright `reportUnnecessaryTypeIgnoreComment`), so `lint` names the ones the upgrade made unnecessary.
   - django-ninja: re-read [Django Ninja mechanisms](../../../docs/typing.md#django-ninja-mechanisms) against the release notes and the linked guide pages; the pin tests under `tests/ninja/` fail when a private behavior changed.

   A schema diff after a django-ninja, pydantic or django-allauth bump is a change in the API: commit it with that upgrade and say so in the report. A django-ninja bump may need changes under `tests/ninja/` only; a failure under `tests/http/` is a change in the HTTP contract: report it and never edit the assertion to make it pass ([docs/development.md](../../../docs/development.md#test-layout)). After a Django or PostgreSQL bump, also run `scripts/check.sh reversible` against a throwaway database ([docs/ci.md](../../../docs/ci.md#reversible-migrations)). After a Django bump, read the release's changes to `django/db/models/deletion.py`: `hard_delete_user` lists the cascade with the undocumented `Collector` ([docs/user-deletion/hard-delete.md](../../../docs/user-deletion/hard-delete.md)).
7. A deprecation warning fails the suite (`filterwarnings = ["error"]`). Fix the call; add a narrow `ignore:` entry only for a third-party warning, with a link to the issue.
8. Run the Bruno smoke ([docs/development.md](../../../docs/development.md#bruno)) after any bump of Django, django-ninja, django-allauth or the images.
9. Push, open the pull request and wait for every CI job, `audit` included. Report per commit: component, from → to, what you read, and what changed besides the pin.

### Python minor release

Dependabot ignores it. In one commit, move together: `.python-version`, `requires-python` and the classifier, ruff `target-version`, mypy `python_version`, pyright `pythonVersion`, `python-version` in `.github/actions/setup-python-env/action.yml`, the `FROM` tag and digest of both Dockerfiles, and the docs that name the version (`rg -n '3\.14' .`). Check first that every dependency ships wheels for the new version.

### PostgreSQL or Valkey major

Not a tag change alone. The plan covers the dump and restore (or `pg_upgrade`) for existing deployments, a note in `docs/deploy.md`, and a test run against the new image. Never bump it as part of a patch tier.
