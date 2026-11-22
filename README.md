# Agentic Django

A Django Ninja template by don-linux, based on the [HackSoft Django Styleguide](https://github.com/HackSoftware/Django-Styleguide). It is a pure JSON API for clients: no HTML API views, and no i18n (`USE_I18N` is off). Registration is open; accounts verify by email code. The Django admin and the staff schema UI at `/api/v1/docs/` are the only HTML pages.

## What it ships

- **Python 3.14** and **Django 6.1**, installed and locked with **uv**
- **Django Ninja**, with an OpenAPI 3.1 schema (`docs/openapi.json`) and Swagger UI (staff session at `/api/v1/docs/`)
- **django-allauth** in headless mode: signup, login, email verification by code, and password reset over JSON
- **PostgreSQL 18**
- **Valkey**, on by default: cache, session cache (`cached_db`), and the Celery broker
- **Celery**, wired, with a worker that runs by default. The only shipped task is `core.ping`; there are no business tasks
- **Gunicorn** and **WhiteNoise** in production
- **Docker Compose** for development (Django, the Celery worker, PostgreSQL, Valkey, and Mailpit) and production (the same stack without Mailpit; TLS stays on a host proxy). An optional Dev Container uses the dev compose file
- A **Bruno** collection for the API (request names in Spanish)
- **GitHub Actions** CI: secrets scan, lint, tests, production image, and a dependency audit
- **Sentry**, optional (empty `SENTRY_DSN` sends nothing)

## Start here

- After copying the repo, follow the ordered checklist in [docs/NEXT-STEPS.md](docs/NEXT-STEPS.md). It starts with renaming the placeholders (`project`, `Project`, `project-name`, `example.com`, …) with the `personalize-django-template` skill.
- Development: [docs/development.md](docs/development.md)
- Production: [docs/deploy.md](docs/deploy.md)
- Documentation index: [docs/README.md](docs/README.md)

## License

[MIT](LICENSE)
