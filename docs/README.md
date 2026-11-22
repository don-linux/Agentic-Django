# Template docs

A map of this template's documentation, one topic per file. Mandatory rules for agents live in [../AGENTS.md](../AGENTS.md); step-by-step recipes live in [../.agents/skills/](../.agents/skills/).

## Start here

- [../README.md](../README.md): what the template is and the stack it ships.
- [NEXT-STEPS.md](NEXT-STEPS.md): the checklist after you copy the template, from personalizing it to the production host.
- [development.md](development.md): local work shared by every setup (checks, tests, Bruno, packaging).
- [development/](development/README.md): bringing the stack up with Docker or without it.
- [deploy.md](deploy.md): running the production Compose stack.
- [ci.md](ci.md): GitHub Actions, pins, Dependabot, image scans, docs lint.
- [environment/](environment/README.md): every environment variable and the file that sets it.

## Areas

- [ARCHITECTURE.md](ARCHITECTURE.md): layers, caching, tasks, authentication, settings map, deployment topology.
- [api.md](api.md): HTTP conventions, errors, pagination, throttling, OpenAPI.
- [typing.md](typing.md): pyright and mypy, what each catches, suppressions, and the upstream workarounds.
- [settings.md](settings.md): how the settings modules and env files are read.
- [security.md](security.md): secrets, Valkey, Sentry, HTTPS, field-encryption recipe.
- [auth/](auth/README.md): optional authentication features (MFA, phone, OAuth) and how to turn them on.
- [user-deletion/](user-deletion/README.md): deactivating, anonymizing and deleting users.
- [email.md](email.md): Mailpit, SMTP, templates, deliverability.
- [files.md](files.md): uploads, permissions, optional S3.
- [tasks.md](tasks.md): writing a task, the worker, beat.
- [data.md](data.md): opt-in data recipes (append-only tables, admin audit log).
- [openapi.json](openapi.json): the committed API schema (OpenAPI 3.1.0).
- [decisions/](decisions/README.md): why the template is shaped this way, one short record per decision; read before undoing one.
