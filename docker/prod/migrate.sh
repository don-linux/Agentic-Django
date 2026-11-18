#!/bin/sh
# Apply migrations. start.sh runs this unless DJANGO_MIGRATE_ON_START=false.
# See docs/deploy.md#migrate.
set -eu

# Migrations may run longer than a request but must not wait forever on a lock.
POSTGRES_STATEMENT_TIMEOUT_MS=0 \
    POSTGRES_LOCK_TIMEOUT_MS="${DJANGO_MIGRATE_LOCK_TIMEOUT_MS:-10000}" \
    python manage.py migrate --noinput
