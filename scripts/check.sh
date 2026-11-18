#!/usr/bin/env bash
# The lint and test gate for local runs and CI: `lint`, `test`, `reversible`, or
# lint then test with no argument. See docs/development.md#checks.
set -euo pipefail

cd "$(dirname "$0")/.."
# The settings CI uses; docs/openapi.yaml is generated with them.
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings.test}"

run_lint() {
    echo "== ruff"
    ruff check .
    ruff format --check .
    echo "== cache.clear()"
    # On Valkey clear() is FLUSHDB: it wipes sessions, rate limits and throttles,
    # whatever the key prefix. Only conftest.py empties the (in-memory) test caches.
    python scripts/forbid_cache_clear.py apps core config
    echo "== mypy"
    mypy .
    echo "== import contracts"
    # [tool.importlinter] in pyproject.toml; docs/ARCHITECTURE.md#import-contracts.
    lint-imports
    echo "== django-upgrade"
    find . \( -path ./.git -o -path ./.venv -o -path './.*_cache' -o -path ./staticfiles -o -path ./media \) -prune \
        -o -name '*.py' -exec django-upgrade --target-version 6.1 --check {} +
    echo "== OpenAPI schema"
    local schema="${TMPDIR:-/tmp}/openapi-check.yaml"
    python manage.py spectacular --file "$schema" --validate --fail-on-warn
    if ! diff -u docs/openapi.yaml "$schema"; then
        echo "docs/openapi.yaml is out of date. Regenerate it with:" >&2
        echo "  python manage.py spectacular --settings config.settings.test --file docs/openapi.yaml --validate --fail-on-warn" >&2
        return 1
    fi
}

run_test() {
    echo "== migrations"
    python manage.py makemigrations --check --dry-run
    echo "== pytest"
    coverage run -m pytest
    coverage report
}

# Throwaway names only (ci, test, ci_*, test_*): unapplying users unapplies
# every app that depends on it. See docs/ci.md#reversible-migrations.
is_throwaway_database() {
    case "$1" in
        ci|test|ci_*|test_*) return 0 ;;
        *) return 1 ;;
    esac
}

run_reversible() {
    # Always the test settings. Never inherit local or production from the shell.
    export DJANGO_SETTINGS_MODULE=config.settings.test
    echo "== reversible migrations"
    local db_name
    db_name="$(python -c "
import django

django.setup()
from django.conf import settings

print(settings.DATABASES['default']['NAME'])
")"
    if ! is_throwaway_database "$db_name"; then
        echo "scripts/check.sh reversible refuses to run against database '$db_name'." >&2
        echo "It unapplies local migrations and would wipe a development database." >&2
        echo "Point DATABASE_URL or POSTGRES_DB at a throwaway name (ci, test, or starting with ci_ / test_)." >&2
        return 1
    fi
    local app
    for app in $(python scripts/local_apps_with_migrations.py); do
        python manage.py migrate --noinput
        python manage.py migrate "$app" zero --noinput
        python manage.py migrate --noinput
    done
}

case "${1:-all}" in
    lint) run_lint ;;
    test) run_test ;;
    reversible) run_reversible ;;
    all)
        run_lint
        run_test
        ;;
    *)
        echo "usage: $0 [lint|test|reversible]" >&2
        exit 2
        ;;
esac
