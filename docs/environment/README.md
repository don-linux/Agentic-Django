# Environment variables

Which file each environment reads and how to change a value. The full lists are per environment:

- [development.md](development.md): the root `.env` and `docker/dev/.env`.
- [production.md](production.md): `docker/prod/.env`.

No file is shared between environments. Development keeps Django's values in the root `.env` and the Compose values (project name, published ports, the container UID) in `docker/dev/.env`. Production keeps everything in `docker/prod/.env`. Each file starts as a copy of its `.env.example`, which is tracked; the copies are ignored by Git and never committed. `core/tests/test_env_reference.py` fails when an example and these pages disagree, or when the code reads a variable that no page lists.

To change a value, edit the file and recreate the containers: [NEXT-STEPS.md#environment-variables](../NEXT-STEPS.md#environment-variables).

## Precedence

Which value wins when the same variable is set in more than one place, from strongest to weakest:

1. The process environment: a variable exported in the shell, or one Compose sets under `environment:`. Compose's `environment:` beats its `env_file`, so a value the compose file pins (such as `DJANGO_SETTINGS_MODULE` in production) cannot be changed from the env file.
2. The services' `env_file`: the root `.env` in development, `docker/prod/.env` in production.
3. The root `.env` that `config/settings/local.py` reads when Django runs outside Docker (`config/settings/_env_file.py`). It never replaces a variable that is already set. Production and test settings never read a `.env` file.
4. The default of the variable's field in `config/settings/env.py`, when there is one. A field with no default is required: settings refuse to load without it ([settings.md#environment-variables](../settings.md#environment-variables)).

`${VAR}` inside a compose file is resolved before any container starts: from the shell first, then from the `.env` next to that compose file (`docker/dev/.env` or `docker/prod/.env`).
