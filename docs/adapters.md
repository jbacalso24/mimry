# MIMRY Adapter Plugins

MIMRY should understand a repo through small, inspectable adapter plugins before it adds semantic embeddings. Embeddings improve fuzzy recall, but adapters define the structured facts coding agents need: files, symbols, routes, imports, schemas, configs, docs, and verification commands.

## Current active adapters

| Adapter | Parser | Extensions | Emits | Agent use |
|---|---|---|---|---|
| `python-ast` | Python stdlib `ast` | `.py` | files, symbols, imports, defines edges, line ranges | Backend/service/test navigation without reading every Python file |
| `typescript-ast` | Tree-sitter language pack | `.js`, `.jsx`, `.ts`, `.tsx` | files, symbols, imports, exports, defines edges, JSX elements, line ranges | Frontend agents can find React/TypeScript components, functions, classes, imports, JSX usage, and edit locations |
| `config-manifest` | Manifest-specific safe metadata parsers | `package.json`, `pyproject.toml`, `tsconfig.json`, `vite.config.*`, `next.config.*`, Expo config, `.env.example`, agent docs | commands, package manager, frameworks, entrypoints, env names, repo rules | Operating context for setup/build/test/rules without replacing Graphify code navigation or indexing secret values |
| `nextjs-app-router` | Filesystem router detector + TS facts | `src/app/**/page.tsx`, `layout.tsx`, `route.ts`, loading/error/not-found | routes, layouts, API routes, dynamic segments, verification hints | Maps App Router pages/layouts/API routes so web agents can jump to actual route files |
| `fastapi` | Python AST decorator detector | `.py` | API routes, HTTP methods, endpoint symbols, route edges | Finds FastAPI endpoints and source functions without executing the app |
| `react-native-expo` | Expo config + Expo Router detector | `app.json`, `app.config.*`, `app/**/*.tsx`, screen files | mobile surfaces, Expo routes, screens, native config hints | Surfaces mobile routes/screens and native config hints without simulator access |
| `sql-schema` | Conservative SQL text parser | `.sql`, Python SQL strings | tables, columns, schema symbols, migration hints | Exposes straightforward SQLite/SQL schemas without executing migrations or DB writes |
| `markdown-docs` | Markdown heading/frontmatter/link parser | `.md`, `.mdx` | headings, frontmatter, markdown links, wiki links, doc relationship hints | Turns docs/specs/Obsidian notes into citable structured facts without storing full docs |
| `generic-text` | Safe text hints | fallback | files, metadata, content hints | Docs/config fallback while keeping sensitive files skipped |

## Planned adapters before/alongside embeddings

Priority order:

1. `swift-ios` — native iOS targets, entitlements, share extensions, App Groups.
2. `sql-alembic` — richer migration lineage and schema-version facts on top of active `sql-schema`.
3. `js-ts-regex` — fallback path if tree-sitter is unavailable in a constrained environment.

Run:

```bash
uv run mimry adapters
uv run mimry adapters --active-only
```

MCP tool:

```text
mimry_list_adapters(active_only?: bool)
```

## Plugin contract

A MIMRY adapter plugin should be narrow and deterministic.

Input:

```text
root: Path
path: Path
safe text reader / binary guard
```

Output should normalize into these record families:

```text
FileInfo
SymbolInfo
ImportInfo
ExportInfo
DependencyEdge
RouteInfo
SchemaInfo
ConfigInfo
DocInfo
```

Each adapter should report:

```text
name
version
kind
extensions / path matchers
parser backend
emitted record families
network behavior: none by default
agent_use: one-line coding-agent routing explanation
```

## Coding-agent usage

Coding agents should use adapter info like a route table:

- Backend auth question → prefer `python-ast` + `fastapi`.
- React screen/component question → prefer `typescript-ast` + `nextjs-app-router` or `react-native-expo`.
- DB/schema question → prefer `sql-schema`.
- Setup/build question → include active `config-manifest` operating context alongside Graphify results.
- Product/spec question → prefer `markdown-docs`.

The MCP surface makes this visible to agents before they decide whether to call `mimry_find`, `mimry_context`, or fall back to direct file inspection.
