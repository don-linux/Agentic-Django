# CI

How the GitHub Actions workflows check the template. Local commands are in [development.md#checks](development.md#checks).

## Workflows

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs on pull requests and pushes to `main`:

- `secrets`: scans the whole Git history with gitleaks ([Secrets](#secrets)).
- `lint`: pre-commit, then `scripts/check.sh lint`.
- `links`: internal Markdown links and heading anchors, offline ([Docs lint](#docs-lint)).
- `api-contract`: pull requests only. oasdiff compares `docs/openapi.json` with the base branch's copy and fails on a breaking change ([api.md#breaking-changes](api.md#breaking-changes)). The first pull request still finds `docs/openapi.yaml` on `main` and applies a temporary `--err-ignore` for four classified document-version and component-rename findings; the next pull request must delete `.github/oasdiff-yaml-baseline-err-ignore.txt` and the ignore branch in `ci.yml`.
- `test`: `scripts/check.sh test` against PostgreSQL (the tests marked `valkey` also use a Valkey service), then `scripts/check.sh reversible`.
- `docker`: validates the production and development Compose files with the example values, builds the production image, scans it with Trivy, runs `manage.py check --deploy`, and smoke-tests it against a password-protected Valkey.
- `bruno`: the Bruno smoke requests against `runserver` ([Bruno smoke](#bruno-smoke)).

A new push to a pull request cancels the run still in progress for it; pushes to `main` never cancel each other. Every checkout drops the GitHub token after cloning (`persist-credentials: false`), and multi-line steps run under `set -euo pipefail`.

[`.github/workflows/audit.yml`](../.github/workflows/audit.yml) audits the dependencies ([Audit](#audit)). [`.github/workflows/links.yml`](../.github/workflows/links.yml) checks the external links every Monday ([Docs lint](#docs-lint)).

### Lint

pre-commit runs every hook, then `scripts/check.sh lint` runs ruff, the `cache.clear()` guard, mypy, the import contracts (`lint-imports`), `django-upgrade --check`, the OpenAPI export (`export_openapi_schema`, its stdout redirected to a temporary file) and a diff against the committed `docs/openapi.json`.

### Secrets

The gitleaks CLI is pinned by version and checksum and scans the full history. `gitleaks-action` is not used: it needs a license for repositories owned by an organization.

#### Gitleaks config

`.gitleaks.toml` allows only the known placeholders.

### Test

The tests marked `valkey` fail, never skip, when the Valkey service is missing.

### Reversible migrations

CI unapplies and reapplies each local app's migrations, so every `RunPython` needs a reverse, and a broken reverse is found here instead of during a rollback. `scripts/local_apps_with_migrations.py` lists the apps from disk, with no database. Locally, `scripts/check.sh reversible` runs the same loop with the test settings, and only against a throwaway database named `ci`, `test`, `ci_*` or `test_*`: unapplying `users` also unapplies every app that depends on it, so it refuses the dev database (`project_db`).

### Docker compose validation

The production Compose file must parse with the example values alone, with and without the `beat` profile. The example is copied to `docker/prod/.env`, as a deploy does, so the services' `env_file` is read too: `--no-env-resolution` cannot skip the copy, because Compose 2.38 loads env files before it looks at that flag.

The step unsets the workflow's `POSTGRES_*` and `DJANGO_SETTINGS_MODULE` first: Compose interpolation prefers the shell over `docker/prod/.env`, so CI's own values would hide a missing or broken example. It then checks that `postgres_db` received the example's `POSTGRES_DB`, `POSTGRES_USER` and `POSTGRES_PASSWORD`, printing names only. The development Compose file, with and without the Dev Container override, must parse with `.env.example` and `docker/dev/.env.example`.

### Docker smoke

- A fixed HIGH or CRITICAL vulnerability in an OS or Python package fails the build; bump the base image digest or the package to clear it.
- A service cannot override its command, so the image's flags are appended, and the throwaway Valkey password is repeated in the steps that start django. The first container is left running for the next request.
- A failed login must answer 400, never 500, and leave allauth's rate-limit counters in Valkey: a cache that failed open would also answer 400, and only the missing counters show it.
- The same image runs the Celery worker with the production settings: it must reach the broker, answer the healthcheck's ping, run a task and log it as one JSON object. No request enqueued that task, so its task id stands in for the request id.

### Docs lint

markdownlint-cli2 runs as a pre-commit hook, so the lint job's pre-commit step checks every Markdown file (`.markdownlint-cli2.yaml`: `MD013` off, `MD024` siblings only). The `links` job runs lychee offline: internal links and heading anchors only (`lychee.toml`), so a third-party outage cannot fail a pull request. `links.yml` checks the external URLs every Monday and on demand (`workflow_dispatch`), with retries, and counts a 429 as reachable; a red run there is a heads-up, not a merge gate. Both pin `lycheeVersion` next to the action SHA; bump them together. Locally, `lychee --offline` does the same. lychee reads only Markdown, so `core/tests/test_docs_anchors.py` checks the `docs/<file>.md#<anchor>` pointers everywhere else (code comments, Compose, scripts, env examples) against the headings.

### Bruno smoke

The `bruno` job migrates a fresh database, runs `seed_dev` with the local settings (no `.env` file; the workflow env holds every value), starts `runserver`, and runs the requests tagged `smoke` with the `seed` environment ([development.md#bruno](development.md#bruno)). None of them sends email, so the job has no Mailpit.

## Required checks

The jobs to mark as required in the branch protection rule for `main` (or a ruleset): `secrets`, `lint`, `links`, `api-contract`, `test`, `docker` and `bruno` from `ci.yml`, and `audit` from `audit.yml`. GitHub lists a check only after it has run once on the repository. A required `audit` blocks every merge while a pinned dependency has a known vulnerability, which is the rule in [Audit](#audit): bump the pin, or ignore by its ID an advisory that does not apply. `audit` never leaves the required checks.

The rule for `main` (Settings → Branches), or a branch ruleset (Settings → Rules → Rulesets), takes these settings. A private repository needs a paid plan (GitHub Pro, Team or Enterprise) for either.

- **Require status checks to pass**, with the eight jobs above, each bound to the GitHub Actions app (ID 15368) so a status of the same name from another source cannot satisfy it. Leave "Require branches to be up to date before merging" off (`strict: false`): CI runs again on the push to `main` after the merge.
- **No required reviews.** The template has one maintainer, and GitHub does not let the author approve their own pull request, so a required review would block every merge.
- **Administrators can bypass** (`enforce_admins: false`), so in an emergency the owner can still merge with a red or missing check, for example while GitHub Actions is down.
- **Force pushes and deletion of `main` stay blocked**, the default for a new rule.

The same rule from the command line, as a repository admin. `PUT` replaces the whole rule, so send every field:

```bash
gh api -X PUT repos/<owner>/<repo>/branches/main/protection --input - <<'EOF'
{
  "required_status_checks": {
    "strict": false,
    "checks": [
      {"context": "secrets", "app_id": 15368},
      {"context": "lint", "app_id": 15368},
      {"context": "links", "app_id": 15368},
      {"context": "api-contract", "app_id": 15368},
      {"context": "test", "app_id": 15368},
      {"context": "docker", "app_id": 15368},
      {"context": "bruno", "app_id": 15368},
      {"context": "audit", "app_id": 15368}
    ]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF
```

`gh api repos/<owner>/<repo>/branches/main/protection` reads it back. A ruleset gets the same settings: target the default branch; add "Require status checks to pass" with the eight checks from GitHub Actions and "Require branches to be up to date before merging" off; keep "Restrict deletions" and "Block force pushes"; add no pull request rule; and put the Repository admin role on the bypass list. A ruleset binds administrators too, so that bypass entry is what `enforce_admins: false` does in a classic rule. A renamed job is a new check name: change the rule together with the pull request that renames it, or every merge waits for a check that never reports.

## Audit

`.github/workflows/audit.yml` is separate from CI, so the weekly schedule audits dependencies without re-running lint, tests and the image build. It needs only uv: `uv export` reads `uv.lock` without installing anything, and the hashes let `pip-audit` skip pip's resolution and audit exactly the locked versions, optional extras included.

It runs on pull requests, pushes to `main`, and every Monday, so new advisories surface without a push. Run it locally with:

```bash
uv export --locked --no-emit-project --all-extras --format requirements.txt -o /tmp/requirements.txt
uvx pip-audit==2.10.1 --strict --disable-pip -r /tmp/requirements.txt
```

When an advisory does not apply to this project, ignore that one advisory, never the check: add `--ignore-vuln <ID>` (its PYSEC, GHSA or CVE ID) to the `pip-audit` command in `audit.yml`, with a comment that says why it does not apply and the date it expires. Never delete or skip the job, and never drop it from the [required checks](#required-checks).

## Dependabot

Dependabot opens at most 5 pull requests per ecosystem each week, for versions at least 7 days old (`cooldown.default-days: 7` in `.github/dependabot.yml`); security updates ignore both the schedule and the cooldown. This 7-day cooldown applies only to Dependabot. Manual upgrades of this template may pin the latest stable release without waiting. Composite actions under `.github/actions/` need their own directory entry. The Python image gets patch updates only. Major PostgreSQL and Valkey upgrades are ignored: a major PostgreSQL needs a dump and restore, and a new Valkey may write data files the old one refuses to load, so changing the tag back does not undo it.

What Dependabot skips (the [tool pins](#tool-pins), the service images in `ci.yml`, Python minor releases, PostgreSQL and Valkey majors, end-of-life dates), and everything after a long pause, is covered by the [maintain-django-template](../.agents/skills/maintain-django-template/SKILL.md) skill: a read-only audit first, then a tiered upgrade plan.

## Tool pins

Dependabot does not bump these, so they move together, by hand:

- uv: `.github/actions/setup-python-env`, both Dockerfiles (`COPY --from`, which Dependabot does not update; the image is pinned by tag and digest, so update both, from `docker buildx imagetools inspect ghcr.io/astral-sh/uv:<version>`) and the uv pre-commit hook. `core/tests/test_compose.py` fails when they differ. The same action sets Python 3.14 (`UV_PYTHON`) and caches the uv-managed Python.
- gitleaks: the pre-commit hook and the version and checksum in `ci.yml`.
- oasdiff: the version and checksum in `ci.yml` (`api-contract` job) and the command in [api.md#breaking-changes](api.md#breaking-changes).

Dependabot does bump ruff and django-upgrade, but twice, in separate pull requests: the pre-commit hook and the `dev` group of `pyproject.toml`. Land both together; `core/tests/test_compose.py` fails while they differ.

## Image pins

Each Dockerfile builds every stage on its first `FROM`, the only one Dependabot updates ([deploy.md#image](deploy.md#image)). The service images in `ci.yml` (Postgres and Valkey) must match both compose files: Dependabot does not update service images in workflows, and `core/tests/test_compose.py` fails when they differ.
