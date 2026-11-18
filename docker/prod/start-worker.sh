#!/bin/sh
set -eu

# Compose pins the module; this also stops a `docker run` or an image ENV
# override from starting production on development settings (DEBUG on, no guards).
if [ "${DJANGO_SETTINGS_MODULE:-}" != "config.settings.production" ]; then
    echo "DJANGO_SETTINGS_MODULE must be config.settings.production" >&2
    exit 1
fi

# Settings import already refuses unsafe values (config/settings/guards.py).
# ERROR level, as in start.sh: a deploy warning must not crash-loop the worker.
python manage.py check --deploy --fail-level ERROR

# No migrations here: the django service applies them first. --quiet drops the
# banner. See docs/tasks.md#the-worker-in-production.
exec celery --app config --quiet worker \
    --hostname "worker@%h" \
    --loglevel "${CELERY_WORKER_LOG_LEVEL:-INFO}" \
    --without-gossip --without-mingle
