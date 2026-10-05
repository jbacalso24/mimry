# MIMRY Adapter Plugins

MIMRY should understand a repo through small, inspectable adapter plugins before it adds semantic embeddings. Embeddings improve fuzzy recall, but adapters define the structured facts coding agents need: files, symbols, routes, imports, schemas, configs, docs, and verification commands.

## Current active adapters

| Adapter | Parser | Extensions | Emits | Agent use |
|---|---|---|---|---|
| `python-ast` | Python stdlib `ast` | `.py` | files, symbols, imports, defines edges, line ranges | Backend/service/test navigation without reading every Python file |
| `typescript-ast` | Tree-sitter language pack | `.js`, `.jsx`, `.ts`, `.tsx` | files, symbols, imports, exports, defines edges, JSX elements, line ranges | Frontend agents can find React/TypeScript components, functions, classes, imports, JSX usage, and edit locations |
| `tree-sitter` | Tree-sitter language pack | `.go`, `.rs`, `.cs`, `.java`, `.php`, `.c`, `.h`, `.cc`, `.cpp`, `.cxx`, `.hh`, `.hpp`, `.hxx`, `.rb`, `.kt`, `.kts`, `.scala`, `.sc`, `.swift` | files, symbols, imports, defines edges, call edges, inheritance edges, line ranges | Backend/systems agents find Go, Rust, C#, Java, PHP, C, C++, Ruby, Kotlin, Scala, and Swift functions, classes, methods, imports, calls, and inheritance relationships |
| `config-manifest` | Manifest-specific safe metadata parsers | `package.json`, `pyproject.toml`, `tsconfig.json`, `vite.config.*`, `next.config.*`, Expo config, `.env.example`, agent docs | commands, package manager, frameworks, entrypoints, env names, repo rules | Operating context for setup/build/test/rules without replacing graph code navigation or indexing secret values |
| `nextjs-app-router` | Filesystem router detector + TS facts | `src/app/**/page.tsx`, `layout.tsx`, `route.ts`, loading/error/not-found | routes, layouts, API routes, dynamic segments, verification hints | Maps App Router pages/layouts/API routes so web agents can jump to actual route files |
| `fastapi` | Python AST decorator detector | `.py` | API routes, HTTP methods, endpoint symbols, route edges | Finds FastAPI endpoints and source functions without executing the app |
| `react-native-expo` | Expo config + Expo Router detector | `app.json`, `app.config.*`, `app/**/*.tsx`, screen files | mobile surfaces, Expo routes, screens, native config hints | Surfaces mobile routes/screens and native config hints without simulator access |
| `sql-schema` | Conservative SQL text parser | `.sql`, Python SQL strings | tables, columns, schema symbols, migration hints | Exposes straightforward SQLite/SQL schemas without executing migrations or DB writes |
| `markdown-docs` | Markdown heading/frontmatter/link parser | `.md`, `.mdx` | headings, frontmatter, markdown links, wiki links, doc relationship hints | Turns docs/specs/Obsidian notes into citable structured facts without storing full docs |
| `office-document` | ZIP container parser over Office XML | `.docx`, `.xlsx` | files, content, table references | Extracts text from Word and Excel documents for indexing and secret scanning without external services |
| `pdf-document` | pypdf text stream extractor | `.pdf` | files, content, table references | Extracts text from PDF files for indexing and secret scanning without OCR or vision models |
| `svg-document` | XML parser over title, desc, text, tspan | `.svg` | files, content | Extracts human-readable text from SVG diagrams for indexing and searching without rendering or rasterizing |
| `js-ts-regex` | Regex-based declaration scanner | `.js`, `.jsx`, `.ts`, `.tsx` | files, symbols, imports, exports, defines edges, line ranges | Fallback when tree-sitter is unavailable; recovers top-level declarations |
| `sql-alembic` | Python AST Alembic migration detector | `.py` | migration facts, revises edges, table operations | Detects Alembic migrations and their lineage; creates edges from migrations to parent migrations |
| `swift-ios` | Xcode project, entitlements and plist parsers | `.pbxproj`, `.entitlements`, `.plist` | xcode targets, entitlements, app groups, bundle IDs, reference edges | Maps iOS targets, entitlements, app extensions and App Groups for native mobile work |
| `generic-text` | Safe text hints | fallback | files, metadata, content hints | Docs/config fallback while keeping sensitive files skipped |

Note: Raster images (PNG, JPG) require OCR or vision models and are not supported.

## Adapter status

All major adapters are now active. Run:

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
- Setup/build question → include active `config-manifest` operating context alongside graph results.
- Product/spec question → prefer `markdown-docs`.

The MCP surface makes this visible to agents before they decide whether to call `mimry_find`, `mimry_context`, or fall back to direct file inspection.
