#!/bin/sh
set -eu

# Compose pins the module; this also stops a `docker run` or an image ENV
# override from starting production on development settings (DEBUG on, no guards).
if [ "${DJANGO_SETTINGS_MODULE:-}" != "config.settings.production" ]; then
    echo "DJANGO_SETTINGS_MODULE must be config.settings.production" >&2
    exit 1
fi

# Settings import already refuses example secrets (config/settings/guards.py).
# ERROR level: a deploy warning must not crash-loop a restarting container.
python manage.py check --deploy --fail-level ERROR
# Uploads (public and private): fail now with a fix, instead of a 500 on the first
# upload. The checks run inside `if`, so the message still prints under `set -e`.
for uploads_dir in "$DJANGO_MEDIA_ROOT" "$DJANGO_PRIVATE_MEDIA_ROOT"; do
    if ! mkdir -p "$uploads_dir" 2>/dev/null \
        || ! touch "$uploads_dir/.write-test" 2>/dev/null \
        || ! rm -f "$uploads_dir/.write-test"; then
        echo "uploads folder $uploads_dir is not writable by UID $(id -u);" \
            "on the host run: sudo chown -R $(id -u):$(id -g) <DOCKER_PROD_DJANGO_UPLOADS_PATH>" >&2
        exit 1
    fi
done

# Migrations (docker/prod/migrate.sh). See docs/deploy.md#migrate.
if [ "${DJANGO_MIGRATE_ON_START:-true}" = "true" ]; then
    sh /app/docker/prod/migrate.sh
fi

# Workers, threads, timeouts and logging: docker/prod/gunicorn.conf.py.
exec gunicorn config.wsgi:application --config /app/docker/prod/gunicorn.conf.py
