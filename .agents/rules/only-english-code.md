# Only English code

Everything that ships in the repository is written in English:

- Identifiers: variables, functions, classes, modules, files, folders, routes, database objects (apps, models, fields, tables) and settings.
- Comments and docstrings.
- Log messages, exception messages and validation errors.
- Migration names and commit messages.
- Config files and their comments: `.env.example`, compose files, Dockerfiles, CI.
- Docs: `README.md`, `AGENTS.md` and `docs/`.

The template has no i18n (`USE_I18N = False`). Text that end users read is plain English; do not add `gettext_lazy` or translation files.

Planning and task write-ups may be in another language. When the user names a
domain concept in Spanish or another language, translate it yourself into an
English identifier before writing code, without asking for the English form and
without a glossary. Follow existing models: short PascalCase types, snake_case
fields. Example: "documentos casa" becomes `HouseDocument` (not `DocumentosCasa`).
Do not add `docs/glossary.md`.

Exceptions, Spanish by design:

- The conversation with the user.
- `.agents/skills/personalize-django-template/SKILL.md`, written for the agent that runs it.
- The whole `bruno/` collection: file names, folder names, request display names (`info.name`, folder `name`) and console messages. It is written for the human testing the API, so agents must not translate or rename anything in `bruno/`. The URLs, JSON keys and headers that Bruno sends are still the API's English identifiers.
