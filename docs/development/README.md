# Development setups

Pick one setup. Topics that do not depend on it (checks, tests, Bruno, packaging) are in [../development.md](../development.md).

- [with-docker.md](with-docker.md): the dev Compose stack or the Dev Container. Postgres, Valkey and Mailpit run as containers.
- [without-docker.md](without-docker.md): Django on your machine against a local Postgres, Valkey and SMTP catcher.

## Shared first steps

1. Copy `.env.example` to `.env` in the repository root. It holds the Django settings for development: `config.settings.local` reads it, and the dev Compose stack passes it to the containers.
2. Continue with the guide for your setup.
