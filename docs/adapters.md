# MIMRY Adapter Plugins

MIMRY should understand a repo through small, inspectable adapter plugins before it adds semantic embeddings. Embeddings improve fuzzy recall, but adapters define the structured facts coding agents need: files, symbols, routes, imports, schemas, configs, docs, and verification commands.

## Current active adapters

| Adapter | Parser | Extensions | Emits | Agent use |
|---|---|---|---|---|
| `python-ast` | Python stdlib `ast` | `.py` | files, symbols, imports, defines edges, line ranges | Backend/service/test navigation without reading every Python file |
| `typescript-ast` | Tree-sitter language pack | `.js`, `.jsx`, `.ts`, `.tsx` | files, symbols, imports, exports, defines edges, JSX elements, line ranges | Frontend agents can find React/TypeScript components, functions, classes, imports, JSX usage, and edit locations |
| `generic-text` | Safe text hints | fallback | files, metadata, content hints | Docs/config fallback while keeping sensitive files skipped |

## Planned adapters before/alongside embeddings

Priority order:

1. `config-manifest` — package/pyproject/tsconfig/vite/next/agent rule commands and dependency facts.
2. `nextjs-app-router` — `page.tsx`, `layout.tsx`, `route.ts`, middleware and app-router edges.
3. `react-native-expo` — Expo routes/config/native module/App Group signals.
4. `fastapi` — FastAPI routes, dependencies, schemas, auth/service edges.
5. `sql-alembic` — schema tables, migrations, migration lineage, and DB edit gates.
6. `markdown-docs` — headings, links, specs, plans, decisions, Obsidian-style links later.
7. `swift-ios` — native iOS targets, entitlements, share extensions, App Groups.
8. `js-ts-regex` — fallback path if tree-sitter is unavailable in a constrained environment.

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

- Backend auth question → prefer `python-ast` + future `fastapi`.
- React screen/component question → prefer `typescript-ast` + future `nextjs-app-router` or `react-native-expo`.
- DB/schema question → prefer future `sql-alembic`.
- Setup/build question → prefer future `config-manifest`.
- Product/spec question → prefer future `markdown-docs`.

The MCP surface makes this visible to agents before they decide whether to call `mimry_find`, `mimry_context`, or fall back to direct file inspection.
