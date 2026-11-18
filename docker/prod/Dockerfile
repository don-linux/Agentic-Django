# One reference to the base image, pinned by tag and digest: Dependabot only
# updates the first FROM of a multi-stage Dockerfile, so both stages build on it.
FROM python:3.14.10-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f AS base

FROM base AS builder

# Keep uv in sync with .github/actions/setup-python-env: Dependabot does not
# update images referenced by COPY --from.
COPY --from=ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 /uv /uvx /bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

COPY pyproject.toml uv.lock ./

# Optional extras, space separated: "s3" for DJANGO_STORAGE_BACKEND=s3.
# See docs/files.md#s3-compatible-storage-optional.
ARG UV_EXTRAS=""
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project \
        $(for extra in $UV_EXTRAS; do printf -- '--extra %s ' "$extra"; done)


FROM base

ARG USER_UID=1000
ARG USER_GID=1000

# Debian security updates, and no pip: nothing installs packages at runtime.
# See docs/deploy.md#image.
RUN apt-get update && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall --yes --no-cache-dir pip \
    && groupadd --gid "${USER_GID}" django \
    && useradd --uid "${USER_UID}" --gid django --no-create-home --shell /usr/sbin/nologin django

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings.production \
    DJANGO_MEDIA_ROOT=/app/uploads/public \
    DJANGO_PRIVATE_MEDIA_ROOT=/app/uploads/private \
    DJANGO_PRIVATE_MEDIA_ACCEL_REDIRECT_PREFIX=/protected-media/

COPY --from=builder /app/.venv /app/.venv
COPY . .

# Throwaway values for collectstatic; they must pass the production guards.
# See docs/deploy.md#image.
RUN DJANGO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(50))')" \
    DJANGO_ADMIN_URL=collectstatic-admin/ \
    DJANGO_ALLOWED_HOSTS=api.ci.invalid \
    DJANGO_MAILER_HOST=smtp.ci.invalid \
    DJANGO_DEFAULT_FROM_EMAIL="CI <noreply@ci.invalid>" \
    FRONTEND_URL=https://app.ci.invalid \
    POSTGRES_DB=collectstatic \
    POSTGRES_USER=collectstatic \
    POSTGRES_PASSWORD=collectstatic-db-password \
    VALKEY_URL=redis://valkey.invalid:6379 \
    VALKEY_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" \
    python manage.py collectstatic --noinput \
    && mkdir -p /app/uploads/public /app/uploads/private \
    && chown -R django:django /app/uploads

# Release metadata, after collectstatic so a new commit keeps the layers above.
# compose passes PROJECT_NAME as APP_NAME; CI the commit and the repository URL.
ARG APP_RELEASE=dev
ARG APP_NAME=""
ARG APP_SOURCE_URL=""
ENV APP_RELEASE=${APP_RELEASE}
LABEL org.opencontainers.image.revision="${APP_RELEASE}" \
      org.opencontainers.image.title="${APP_NAME}" \
      org.opencontainers.image.source="${APP_SOURCE_URL}"

USER django

EXPOSE 8000

CMD ["sh", "/app/docker/prod/start.sh"]
