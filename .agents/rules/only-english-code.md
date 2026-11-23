# Only English code

Everything that ships in the repository is written in English:

- Identifiers: variables, functions, classes, modules, files, folders, routes, database objects (apps, models, fields, tables) and settings.
- Comments and docstrings.
- Log messages, exception messages and validation errors.
- Migration names and commit messages.
- Config files and their comments: `.env.example`, compose files, Dockerfiles, CI.
- Docs: `README.md`, `AGENTS.md` and `docs/`.

The template has no i18n (`USE_I18N = False`). Text that end users read is plain English; do not add `gettext_lazy`, translation files, or `LocaleMiddleware`.

Planning and task write-ups may be in another language.

This rule is proactive. When the user names a domain concept in Spanish or
another language, translate it yourself into an English identifier before
writing code, without asking for the English form and without a glossary. Follow
existing models: short PascalCase types, snake_case fields, no filler such as
`Model` or `Data` on the type. Example: "documentos casa" becomes
`HouseDocument` (not `DocumentosCasa`, `HouseDocumentsModel`, or
`Documentos_casa`). If several translations fit, pick one, use it consistently,
and mention the choice when reporting the change. Do not add `docs/glossary.md`.

Exceptions, Spanish by design:

- The conversation with the user.
- `.agents/skills/personalize-django-template/SKILL.md`, written for the agent that runs it.
- The `bruno/` collection's file names, folder names, request display names (`info.name`, folder `name`) and console messages. They are written for the human testing the API, so agents never translate or rename them. The URLs, JSON keys and headers that Bruno sends are the API's English identifiers, and the personalize skill sets the collection's own name (`bruno/opencollection.yml`) and the environments' URLs.
