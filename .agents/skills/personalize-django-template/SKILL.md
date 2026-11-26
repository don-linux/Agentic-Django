---
name: personalize-django-template
description: Personaliza este template Django preguntando nombre visible, slug, nombre de Docker, dominio, base de datos, admin y autor del paquete, y arma un plan para sustituir los placeholders project, Project, project-name, example.com, project_db y el ejemplo de DJANGO_ADMINS. Use when starting a project from this template, naming the project, setting the domain, database, Docker name, or package author, or reversing the generic placeholders.
---

# Personalizar el template Django

Sustituye la identidad genérica del template por la del proyecto. El resultado de esta skill es un plan; no edites archivos hasta que el usuario confirme ese plan.

## Cuándo correr

Corre solo si el repo sigue genérico: `pyproject.toml` tiene `name = "project"`.

Si ya está personalizado, no vuelvas a preguntar salvo que el usuario pida renombrar.

## Placeholders

Todo lo que identifica a un backend usa uno de estos valores, y el plan los sustituye todos:

| Placeholder | Qué es | Dónde aparece |
| --- | --- | --- |
| `project` | Slug Python | `pyproject.toml`, Bruno, el default de `DJANGO_CACHE_KEY_PREFIX` |
| `Project` | Nombre visible | título y descripción de la API (`NINJA_TITLE`, `NINJA_DESCRIPTION`) |
| `project-name` | Nombre de Docker y ruta de uploads | `PROJECT_NAME` de dev y prod, `devcontainer.json`, `DOCKER_PROD_DJANGO_UPLOADS_PATH=/srv/project-name/uploads` y los ejemplos `/srv/project-name` en compose, `docs/NEXT-STEPS.md`, `docs/ARCHITECTURE.md`, `docs/files.md`, `docs/deploy.md` |
| `example.com` | Dominio en *ejemplos y docs* | `docker/prod/.env.example`, `docs/NEXT-STEPS.md`. **No** es placeholder en el código de denylist (ver más abajo) |
| `project_db` | Base de datos | `.env.example`, `docker/prod/.env.example`, `docs/NEXT-STEPS.md` y `docs/ci.md` |
| `admin@example.com` | `DJANGO_ADMINS` (comentado) | `docker/prod/.env.example` |

Lo que es igual en todos los backends no es placeholder y no se toca: el paquete `config` (también `config.wsgi:application`), los servicios `django`, `celery_worker`, `celery_beat`, `postgres_db`, `valkey` y `mailpit`, `apps/`, `core/`, los nombres de archivo `compose.yaml`, los Dockerfiles y el CI.

**No cambies los defaults de denylist en el código.** Deben seguir siendo valores que producción rechaza si faltan las variables de entorno:

- `config/settings/env.py`, `ProductionEnv`: `FRONTEND_URL` default `https://app.example.com` (`DJANGO_ALLOWED_HOSTS` no tiene default: es obligatoria)
- `config/settings/env.py`, `BaseEnv`: `DJANGO_DEFAULT_FROM_EMAIL` default `Project <noreply@example.com>`, `DJANGO_EMAIL_SUBJECT_PREFIX` default `[Project]` (con un espacio al final), `DJANGO_EMAIL_SIGNATURE_NAME` default `Project`
- `config/settings/guards.py`: `EXAMPLE_DOMAINS` y las comprobaciones de dominios de ejemplo

Los valores reales del proyecto van en `docker/prod/.env.example` (y en `.env.example` para lo local). Sí se cambian en código: `NINJA_TITLE` y `NINJA_DESCRIPTION`, y el default de `DJANGO_CACHE_KEY_PREFIX` (el slug: dos proyectos no deben compartir claves en Valkey).

## Preguntar

Pide estos datos antes de proponer cambios:

- Nombre visible. Hoy: `Project`.
- Slug Python: el nombre del paquete en `pyproject.toml` y de la colección de Bruno. Hoy: `project`. Tiene que ser un identificador Python válido. Si no lo dan, derívalo del nombre visible: minúsculas, espacios y guiones a `_`, sin caracteres que no valgan en un identificador, y sin empezar por un número.
- Nombre de Docker. Hoy: `project-name`. Da nombre a los stacks `{docker}-dev` y `{docker}-prod`, al Dev Container y a `/srv/{docker}/uploads`. Solo minúsculas, números, `-` y `_`, empezando por letra o número. Si no lo dan, propón el nombre de la carpeta del repo con esas reglas; si la carpeta todavía se llama como el template, pregunta.
- Dominio. Hoy: `example.com`. Es opcional: si todavía no hay, `example.com` se queda en los *ejemplos pendientes* (no toques los defaults de denylist).
- Base de datos. Hoy: `project_db`. Si no la dan, usa `{slug}_db`.
- Correo (y nombre opcional) de quien recibe los correos de error, para `DJANGO_ADMINS`. Hoy: el ejemplo comentado `admin@example.com`. Si no lo dan, el ejemplo se queda y va a pendientes.
- Zona horaria para mostrar fechas y cortar días (los datos siempre se guardan en UTC). Hoy: `UTC`. Si no la dan, se queda `UTC`. Ejemplo: `America/Mexico_City`.
- Autor del paquete en `pyproject.toml`. Hoy el template solo declara `{ name = "Don Linux" }` (sin correo personal). Pide nombre y, si lo dan, correo del proyecto nuevo, y escríbelos en `authors`. `LICENSE` (copyright "Don Linux") no se toca; el adoptante puede añadir su propia línea de copyright, sin borrar la original.

## Derivar y mostrar

Muestra estos valores, ya resueltos, antes del plan:

- Stacks de Docker: `{docker}-dev` y `{docker}-prod`
- Ruta de uploads: `/srv/{docker}/uploads`
- From (solo en ejemplos de entorno, no en defaults de código): `{Nombre} <noreply@{dominio}>`
- Prefijo de asunto (comentado en el ejemplo de prod): `[{Nombre}]` seguido de un espacio
- Título de la API: `{Nombre} API`
- Descripción de la API (`NINJA_DESCRIPTION`): `The {Nombre} JSON API. Account endpoints under /_allauth/ belong to django-allauth and keep its own format (docs/api.md).`
- Descripción de `pyproject` y del README: `Backend for {dominio}`, o `Backend for {Nombre}` si el dominio está pendiente. El texto actual de `pyproject.toml` es `description = "Django project template"`.
- Producción (solo `docker/prod/.env.example`): `DJANGO_ALLOWED_HOSTS={dominio}`, `DJANGO_CSRF_TRUSTED_ORIGINS=https://app.{dominio}` y `FRONTEND_URL=https://app.{dominio}`

## Plan

El plan actualiza solo estas rutas.

### Django

- `config/settings/base.py`: `NINJA_TITLE`, `NINJA_DESCRIPTION` y el default de `DJANGO_CACHE_KEY_PREFIX` (`{slug}`, el `or "project"` de `CACHE_KEY_PREFIX`).
- `config/settings/env.py` y `config/settings/production.py`: no se tocan (los defaults de denylist se quedan; **no** cambies los de `DJANGO_DEFAULT_FROM_EMAIL`, `DJANGO_EMAIL_SUBJECT_PREFIX`, `DJANGO_EMAIL_SIGNATURE_NAME` ni `FRONTEND_URL`).

### Proyecto

- `pyproject.toml`: `name`, `description` (`Backend for {dominio}` o `Backend for {Nombre}`) y `authors` (nombre y correo que haya dado el usuario)
- `bruno/opencollection.yml`: `info.name: {slug}`
- `.env.example`: `POSTGRES_DB`, `DJANGO_TIME_ZONE`, `DJANGO_EMAIL_SIGNATURE_NAME="{Nombre}"` y las líneas comentadas `DJANGO_EMAIL_SUBJECT_PREFIX="[{Nombre}] "` y `DJANGO_DEFAULT_FROM_EMAIL="{Nombre} <noreply@{dominio}>"` (si el dominio está pendiente, `example.com` se queda y va a pendientes)
- `docker/prod/.env.example`: la línea comentada `# DJANGO_ADMINS=admin@example.com` pasa a `# DJANGO_ADMINS={correo}`; con nombre, `# DJANGO_ADMINS='"{Admin}" <{correo}>'` (comillas simples por Compose)
- `docker/prod/.env.example`: `POSTGRES_DB`, `DJANGO_TIME_ZONE`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `FRONTEND_URL` (`https://app.{dominio}`), `DJANGO_DEFAULT_FROM_EMAIL="{Nombre} <noreply@{dominio}>"`, `DJANGO_EMAIL_SIGNATURE_NAME="{Nombre}"`, `CORS_ALLOWED_ORIGINS` (`https://app.{dominio}`), `DOCKER_PROD_DJANGO_UPLOADS_PATH=/srv/{docker}/uploads` y el comentario `sudo mkdir -p /srv/{docker}/uploads/...`, y la línea comentada `DJANGO_EMAIL_SUBJECT_PREFIX="[{Nombre}] "`. `PROJECT_NAME` va en la sección de Docker
- En los tres `.env.example`, todo valor con espacios va entre comillas dobles, como en el template (`DJANGO_EMAIL_SIGNATURE_NAME="Acme Backend"`). `uv run --env-file .env` deja de leer el archivo en la primera línea sin comillas con espacios (solo avisa): esa variable y todas las que vienen después no se cargan; `core/tests/test_env_examples.py` lo comprueba y el paso 3 lo verifica con el `.env` real.
- Los tests no se editan: no dependen del nombre ni del dominio. `config/settings/test.py` fija `Project` y `example.com` a propósito, y `core/tests/test_production_email_guard.py` usa `Project <noreply@example.com>` y `smtp.example.com` como casos permanentes de denylist.

### Docs

- `README.md`: sustituye el título por `# {Nombre}` y el primer párrafo por `Backend for {dominio}` (o `Backend for {Nombre}` si el dominio está pendiente). El resto del README no se reescribe.
- `AGENTS.md`: borra el punto de `## Init` que manda a correr esta skill (`If pyproject.toml still says name = "project"...`) y, en `## Skills`, quita `— first, when name = "project"` (la skill sigue listada para renombrar más adelante).
- `docs/NEXT-STEPS.md`: `example.com` pasa a `{dominio}`, `project_db` a `{db}` y `/srv/project-name` a `/srv/{docker}`, salvo en el paso «Personalize the placeholders», que describe esta skill y se queda igual
- `docs/ci.md`: en «Reversible migrations», la base de dev de ejemplo (`project_db`) pasa a `{db}`
- `docs/ARCHITECTURE.md`, `docs/files.md` y `docs/deploy.md`: el ejemplo `/srv/project-name` pasa a `/srv/{docker}`
- `docs/ARCHITECTURE.md`: la clave de ejemplo `project:1:health:ping` pasa a `{slug}:1:health:ping`
- `docs/environment/production.md`: el default de `DJANGO_CACHE_KEY_PREFIX` (`project`) pasa a `{slug}`, como en `config/settings/base.py`
- `docs/openapi.json`: no se edita a mano. Es generado y lleva `NINJA_TITLE` y `NINJA_DESCRIPTION`, así que se regenera en el Cierre; si no, `scripts/check.sh lint` falla al comparar el export con el archivo comprometido.

## Docker y Dev Container

`project-name` es el nombre de Docker del template. Sustitúyelo por `{docker}` en:

- `.devcontainer/devcontainer.json`: `"name": "{docker}"`
- `docker/dev/.env.example` y `docker/prod/.env.example`: `PROJECT_NAME={docker}`
- `docker/dev/.env`, si existe: la misma línea. Solo tiene puertos, este nombre, `HOST_UID` y `HOST_GID`, y es el archivo que Compose lee. Si se queda en `project-name`, el stack sigue siendo `project-name-dev` y se comparte con el template y con cualquier otra copia.
- El texto de ejemplo en `docker/prod/compose.yaml` (`e.g. /srv/project-name/uploads` → `/srv/{docker}/uploads`). Los `compose.yaml` no se reescriben en lo demás: leen `${PROJECT_NAME}` y resuelven `{docker}-dev` y `{docker}-prod`.

`docker/dev/.env` es el único `.env` local que se edita. El `.env` de la raíz y `docker/prod/.env` llevan secretos: no se tocan.

Avisa en el plan que cambiar `PROJECT_NAME` crea un stack y un volumen de Postgres nuevos. Si ya había un `project-name-dev` levantado desde esta carpeta, se baja a mano con `docker compose -p project-name-dev down`, y `-v` solo si sus datos no importan.

## Cierre

Después de editar, en este orden:

1. `uv lock`. El lock guarda el nombre del paquete, y los Dockerfiles y el CI usan `--locked`, que falla si no coincide con `pyproject.toml`. (No lo corras con `UV_FROZEN=1`.)
2. Barrido de placeholders:

   ```bash
   rg -n --hidden -g '!.git' -g '!.venv' -g '!.agents/skills/**' -g '!uv.lock' -g '!docs/openapi.json' \
     -e 'project-name' -e 'project_db' -e 'example\.com' -e '\bProject\b' \
     -e '"project' -e 'name: project$' .
   ```

   Solo pueden quedar, y no se «arreglan»:

   - Correos y hosts de prueba `@example.com` (y `not-allowed.example`, etc.) en `apps/**/tests/`, `core/tests/` y `bruno/environments/local.yml`
   - `you@example.com` en `docker/prod/.env.example` y en el ejemplo de `sendtestemail` (`docs/deploy.md`)
   - El comentario de formato `ops@example.com` en `docker/prod/.env.example` (es un ejemplo de sintaxis de Compose, no el admin del proyecto)
   - El comentario `# Project` de `.gitignore`
   - `POSTGRES_DB=project_db` en el `.env` de la raíz, si ya existe: va a pendientes
   - `example.com` y `admin@example.com` donde el dominio o `DJANGO_ADMINS` quedaron pendientes (solo en ejemplos de entorno y docs, no en el código de denylist que debe quedarse)
   - Defaults de denylist en `config/settings/env.py` (From / asunto / firma y el `FRONTEND_URL` de producción) y `config/settings/guards.py`
   - Casos de denylist en `core/tests/test_production_email_guard.py` (incluye `Project <noreply@example.com>` y `smtp.example.com`)
   - `config/settings/test.py` (`Project` y `example.com` fijos para los tests) y los valores fijos de fixtures en `apps/**/tests/` y `core/tests/` (`project_db`, `${PROJECT_NAME:-project-name}` en `core/tests/test_compose.py`)
   - Los fallbacks `${PROJECT_NAME:-project-name}` de `docker/dev/compose.yaml` y `docker/prod/compose.yaml`, y su mención en `docs/development/with-docker.md`: solo actúan sin `PROJECT_NAME`
   - Las columnas de default de `docs/environment/development.md` y `docs/environment/production.md` (`Project`, `[Project]`, `project-name`): documentan los defaults de código, que no cambian. La fila de `DJANGO_CACHE_KEY_PREFIX` sí cambia, porque su default de código cambia
   - Los ejemplos de sintaxis que no nombran el producto: `ada@example.com` en `docs/development.md`, `api.example.com,www.example.com` en `docs/settings.md`, y el comentario `no example.com default` de `config/settings/production.py`
   - La línea de `docs/settings.md` que dice que los tests fijan los valores del template (`Project values are pinned to the template defaults`)
   - La sección `## Personalize` de `docs/development.md` (tabla y pasos manuales), el paso «Personalize the placeholders» de `docs/NEXT-STEPS.md` y la línea del checklist en `README.md`: describen esta skill
   - `nobody@example.com` de `bruno/Cuentas/iniciar-sesion-invalido.yml` y `user@example.com` de `bruno/environments/seed.yml` (el usuario de `seed_dev`)
   - Los enlaces de ejemplo `https://example.com/docs/...` de las recetas de `docs/api.md` (deprecación)
   - Los comentarios de `docker/prod/.env.example` que explican la guarda (`every example.com / example.org / example.net`, `not example.com, not localhost`, `refuses example.com and localhost`)
   - Usuarios de `apps/users/management/commands/seed_dev.py` (`admin@example.com`, `user@example.com`) y las menciones de esos correos o de «no `example.com` o `localhost`» en los docs (documentan la guarda, no son el dominio del producto)

   El barrido respeta `.gitignore`, así que no abre `.env` ni `docker/dev/.env`: el paso 3 comprueba el nombre del stack de dev. Cualquier otra coincidencia de `project-name`, `project_db` o `/srv/project-name` sí es un placeholder que faltó. Un hit de `example.com` / `Project` en la denylist o en tests no se reescribe.

3. Checks. Preferente, en el contenedor de dev, reconstruido porque cambió el lock (creando `.env` y `docker/dev/.env` desde sus ejemplos solo si no existen):

   ```bash
   [ -f .env ] || cp .env.example .env
   [ -f docker/dev/.env ] || cp docker/dev/.env.example docker/dev/.env
   docker compose -f docker/dev/compose.yaml config | awk '/^name:/{print; exit}'
   # expect: name: {docker}-dev (docker/dev/.env, si ya existía, también se renombró)
   docker compose -f docker/dev/compose.yaml up -d --build
   docker compose -f docker/dev/compose.yaml exec -T django python manage.py export_openapi_schema --settings config.settings.test --api config.api.api --indent 2 > docs/openapi.json
   docker compose -f docker/dev/compose.yaml exec django scripts/check.sh
   if [ ! -f docker/prod/.env ]; then
     cp docker/prod/.env.example docker/prod/.env
     created_prod_env=1
   fi
   docker compose -f docker/prod/compose.yaml config --quiet
   docker compose -f docker/prod/compose.yaml --profile beat config --quiet
   docker compose -f docker/prod/compose.yaml config | awk '/^name:/{print; exit}'
   # expect: name: {docker}-prod
   [ "${created_prod_env:-}" = 1 ] && rm -f docker/prod/.env
   ```

   No uses `--no-env-resolution`. Si `docker/prod/.env` ya existía, úsalo y no lo borres.

   Si no hay daemon de Docker, el equivalente en el host es (con PostgreSQL y Valkey locales y el `.env` de la raíz; ver `docs/development/without-docker.md#checks`):

   ```bash
   [ -f .env ] || cp .env.example .env
   uv run --env-file .env python manage.py export_openapi_schema --settings config.settings.test --api config.api.api --indent 2 > docs/openapi.json
   uv run --env-file .env scripts/check.sh
   uv run --env-file .env python -c 'import os; print(os.environ["DJANGO_EMAIL_SIGNATURE_NAME"])'
   # expect: {Nombre}. Un KeyError es una línea del .env sin comillas con espacios: uv dejó de leer el archivo ahí.
   ```

   `docs/openapi.json` se regenera en el host (o con `compose exec` de forma que el archivo no quede con dueño root). `docs/openapi.json` no entra en el barrido del paso 2 porque es generado.

## Pendientes

Al cerrar, lista lo que el usuario decide y la skill no puede derivar. No lo edites:

- Secretos de producción: `DJANGO_SECRET_KEY`, `DJANGO_ADMIN_URL`, `POSTGRES_PASSWORD` y `VALKEY_PASSWORD`, en un `docker/prod/.env` copiado del ejemplo
- SMTP real: `DJANGO_MAILER_HOST`, `DJANGO_MAILER_USERNAME` y `DJANGO_MAILER_PASSWORD`
- Puertos de `docker/dev/.env`, si va a correr junto a otros backends. `baseUrl` y `mailpitUrl` de `bruno/environments/local.yml` y `seed.yml` tienen que usar `DOCKER_DEV_DJANGO_PORT` y `DOCKER_DEV_MAILPIT_UI_PORT`
- `POSTGRES_DB` en el `.env` de la raíz y en `docker/prod/.env`, si ya existen
- El dominio y `DJANGO_ADMINS`, si quedaron sin respuesta
- Llenar «Domain invariants» en `AGENTS.md` con el responsable del producto, antes de crear apps de dominio. No crees `docs/glossary.md`.

## Fuera del plan

No incluyas estos cambios:

- El paquete `config`
- Correos de los tests y los casos de denylist
- Skills en `.agents/skills` (salvo que esta misma skill se esté corrigiendo en el template)
- Dockerfiles y `.github/` (los hosts de prueba `*.ci.invalid` del CI se quedan)
- `LICENSE`
