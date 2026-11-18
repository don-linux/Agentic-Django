# Pinned by tag and digest; Dependabot updates both.
FROM python:3.14.10-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f

# Keep uv in sync with .github/actions/setup-python-env: Dependabot does not
# update images referenced by COPY --from.
COPY --from=ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 /uv /uvx /bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
        git build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# The container runs as django with your host UID and GID (HOST_UID and HOST_GID
# in docker/dev/.env), so the files it writes to the bind-mounted checkout are
# yours. --non-unique: the GID may already exist in the image (20 on macOS).
ARG HOST_UID=1000
ARG HOST_GID=1000
RUN groupadd --non-unique --gid "$HOST_GID" django \
    && useradd --non-unique --uid "$HOST_UID" --gid "$HOST_GID" \
        --create-home --shell /bin/bash django

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml uv.lock ./

# Built as root (the cache mount belongs to root), then handed to django in the
# same layer, so `uv sync --locked` in the container can update it.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project \
    && chown -R django:django /opt/venv

USER django
