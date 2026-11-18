---
name: graphify
description: Optional local code map of this repository using the Graphify CLI in code-only mode. Use when exploring how apps, core, config or tasks call each other, or when the user asks for a Graphify graph, query, path or explain. Never required. Never use Graphify Cloud or --strict.
---

# Local Graphify (code-only)

This skill is optional. If `graphify` is not installed, continue with the usual tools (`rg`, file reads) and do not block the task. Do not install the tool unless the user asks.

The written source of truth for architecture and decisions remains [docs/ARCHITECTURE.md](../../../docs/ARCHITECTURE.md) and the area docs under `docs/`. The graph only shows what the AST extracted (calls, imports, inheritance). It does not replace those documents.

## Rules

- Run fully offline. Always pass `--code-only` **and** set `GRAPHIFY_BACKEND=none` (disables every LLM backend, even if an API key happens to be in the environment). They are not interchangeable: `--code-only` skips docs/PDFs/images; the env var is the kill-switch.
- Do not use Graphify Cloud (`app.graphify.com`, `api.graphify.com/mcp`), do not log in, and do not upload this private repository.
- Do not run `graphify install --strict`, do not set `GRAPHIFY_HOOK_STRICT=1`, and do not install Graphify git hooks (`graphify hook install`).
- Do not run `graphify install --project` in this repository: it would overwrite this skill with one that pitches Cloud.
- Do not pass `--backend`, `--mcp`, `--neo4j-push`, `--falkordb-push`, or `graphify add <url>`.
- Write output only under `graphify-out/` (gitignored). Do not commit `graph.json`, `GRAPH_REPORT.md`, `graph.html`, or a generated wiki. Do not write into `docs/`.
- Prefer `graphify query`, `graphify path` and `graphify explain` once `graphify-out/graph.json` exists. Then read the files the graph points to. Treat `EXTRACTED` edges as AST facts; treat `INFERRED` / `AMBIGUOUS` edges as hints and confirm them in the source.

## Install (once, on the host)

The PyPI package is `graphifyy` (two y's). The command is `graphify`. Python `>=3.10,<3.15` (this template is on 3.14). Pin 0.9.109, the version tested with this template.

```bash
uv tool install graphifyy==0.9.109
# if the command is not found: uv tool update-shell, then open a new terminal
graphify --version
```

## Build and query

From the repository root:

```bash
export GRAPHIFY_BACKEND=none
graphify extract . --code-only
# writes graphify-out/graph.json (and an AST cache). To also build
# graph.html and GRAPH_REPORT.md, still offline, with Community N labels:
graphify cluster-only .
graphify query "session token authentication"
graphify path "SessionTokenAuthentication" "user_get"
graphify explain "BaseModel"
```

`--force` on extract rebuilds when an incremental update looks stale. Open `graphify-out/graph.html` in a browser for the picture.

See [docs/development.md#graphify](../../../docs/development.md#graphify).
