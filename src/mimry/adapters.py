from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

AdapterStatus = Literal["active", "planned"]
AdapterKind = Literal["code", "document", "data", "media", "project"]


@dataclass(frozen=True)
class AdapterInfo:
    name: str
    status: AdapterStatus
    kind: AdapterKind
    extensions: tuple[str, ...]
    parser: str
    emits: tuple[str, ...]
    agent_use: str
    notes: str

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["extensions"] = list(self.extensions)
        data["emits"] = list(self.emits)
        return data


BUILTIN_ADAPTERS: tuple[AdapterInfo, ...] = (
    AdapterInfo(
        name="python-ast",
        status="active",
        kind="code",
        extensions=(".py",),
        parser="stdlib ast",
        emits=("files", "symbols", "imports", "defines_edges", "line_ranges"),
        agent_use=(
            "Find Python services, tests, functions, classes, imports, and edit locations without"
            " reading every file."
        ),
        notes=(
            "Currently extracts FunctionDef, AsyncFunctionDef, ClassDef, imports, line_start,"
            " line_end."
        ),
    ),
    AdapterInfo(
        name="typescript-ast",
        status="active",
        kind="code",
        extensions=(".js", ".jsx", ".ts", ".tsx"),
        parser="tree-sitter-language-pack",
        emits=(
            "files",
            "symbols",
            "imports",
            "exports",
            "defines_edges",
            "component_edges",
            "call_edges",
            "jsx_elements",
            "line_ranges",
        ),
        agent_use=(
            "Frontend agents can find React/TypeScript components, functions, classes, methods,"
            " imports, JSX usage, call relationships, and edit locations without reading every TSX"
            " file."
        ),
        notes=(
            "Extracts functions, classes, methods, variable/arrow components, JSX element"
            " references, import/export hints (ES and CommonJS require), and line ranges. Call"
            " sites resolve to symbol-to-symbol call edges. Module-scope caller attribution is"
            " exact; a call on a `const x = f()` line is still attributed to the local binding."
        ),
    ),
    AdapterInfo(
        name="config-manifest",
        status="active",
        kind="data",
        extensions=(
            "package.json",
            "pyproject.toml",
            "tsconfig.json",
            "vite.config.*",
            "next.config.*",
            "app.json",
            "app.config.*",
            ".env.example",
            "CLAUDE.md",
            "AGENTS.md",
            "README.md",
        ),
        parser="manifest-specific safe metadata parsers",
        emits=(
            "commands",
            "package_manager",
            "frameworks",
            "entrypoints",
            "env_names",
            "agent_rules",
        ),
        agent_use=(
            "Gives coding agents exact commands, package managers, frameworks, env variable names,"
            " and repo rules before edits without exposing secret values."
        ),
        notes=(
            "Operating-context adapter layered alongside the graph; does not replace code graph"
            " extraction."
        ),
    ),
    AdapterInfo(
        name="nextjs-app-router",
        status="active",
        kind="project",
        extensions=(
            "page.tsx",
            "layout.tsx",
            "route.ts",
            "loading.tsx",
            "error.tsx",
            "not-found.tsx",
        ),
        parser="safe filesystem router detector + TypeScript AST facts",
        emits=("routes", "layouts", "api_routes", "dynamic_segments", "verification_hints"),
        agent_use=(
            "Maps Next.js App Router pages, layouts, loading/error/not-found surfaces, dynamic"
            " segments, and API route files for web agents."
        ),
        notes=(
            "Does not execute Next.js or read env values; emits route facts into file metadata for"
            " ranking/context packs."
        ),
    ),
    AdapterInfo(
        name="fastapi",
        status="active",
        kind="project",
        extensions=(".py",),
        parser="Python AST FastAPI/APIRouter decorator detector",
        emits=("api_routes", "http_methods", "endpoint_symbols", "route_edges"),
        agent_use=(
            "Lets backend agents find FastAPI endpoints, methods, paths, and endpoint functions"
            " without broad Python spelunking."
        ),
        notes=(
            "Detects literal @app/@router HTTP method paths only; no app execution or dependency"
            " resolution."
        ),
    ),
    AdapterInfo(
        name="react-native-expo",
        status="active",
        kind="project",
        extensions=("app.json", "app.config.*", ".tsx", ".ts"),
        parser="Expo config + Expo Router filesystem detector",
        emits=(
            "mobile_surfaces",
            "expo_routes",
            "screens",
            "native_config_hints",
            "app_config_edges",
        ),
        agent_use=(
            "Surfaces Expo Router routes, screen/component files, and native iOS/Android config"
            " hints without simulator access."
        ),
        notes=(
            "Safe manifest/filesystem parsing only; does not run Expo, Metro, xcodebuild, adb, or"
            " simulators."
        ),
    ),
    AdapterInfo(
        name="sql-schema",
        status="active",
        kind="data",
        extensions=(".sql", ".py"),
        parser="conservative CREATE TABLE regex over text/Python string literals",
        emits=("tables", "columns", "schema_symbols", "migration_hints"),
        agent_use=(
            "Finds straightforward SQL/SQLite tables and columns while keeping migrations as"
            " schema evidence instead of runtime edit targets."
        ),
        notes=(
            "Read-only text parsing; never executes migrations, opens databases, or performs"
            " writes."
        ),
    ),
    AdapterInfo(
        name="markdown-docs",
        status="active",
        kind="document",
        extensions=(".md", ".mdx"),
        parser="Markdown heading/frontmatter/link/wiki-link parser",
        emits=("headings", "frontmatter", "doc_links", "wiki_links", "doc_relationship_hints"),
        agent_use=(
            "Turns docs, plans, specs, and Obsidian-style notes into structured context without"
            " overpowering code-edit queries."
        ),
        notes=(
            "Extracts headings, title/type/tags frontmatter, markdown links, and [[wiki links]];"
            " stores no full document body."
        ),
    ),
    AdapterInfo(
        name="office-document",
        status="active",
        kind="document",
        extensions=(".docx", ".xlsx"),
        parser="ZIP container parser over word/document.xml and xl/sharedStrings.xml",
        emits=("files", "content", "table_references"),
        agent_use=(
            "Extracts text from Word and Excel documents for indexing and secret scanning without"
            " external services."
        ),
        notes=(
            "Guards against zip bombs, DTD/entity attacks, and compression bombs; collapses text"
            " to searchable form."
        ),
    ),
    AdapterInfo(
        name="pdf-document",
        status="active",
        kind="document",
        extensions=(".pdf",),
        parser="pypdf text stream extractor",
        emits=("files", "content", "table_references"),
        agent_use=(
            "Extracts text from PDF files for indexing and secret scanning without OCR or vision"
            " models."
        ),
        notes=(
            "Handles empty user passwords on encrypted PDFs; limits to 200 pages and 20 MB file"
            " size."
        ),
    ),
    AdapterInfo(
        name="svg-document",
        status="active",
        kind="document",
        extensions=(".svg",),
        parser="XML parser over title, desc, text, and tspan elements",
        emits=("files", "content"),
        agent_use=(
            "Extracts human-readable text from SVG diagrams for indexing and searching without"
            " rendering or rasterizing."
        ),
        notes=(
            "Extracts title, descriptions, and text elements only; guards against DTD/entity"
            " attacks."
        ),
    ),
    AdapterInfo(
        name="js-ts-regex",
        status="active",
        kind="code",
        extensions=(".ts", ".tsx", ".js", ".jsx"),
        parser="regex import/export scanner fallback",
        emits=("files", "imports", "exports", "symbols", "defines_edges", "line_ranges"),
        agent_use=(
            "Fallback path when tree-sitter is unavailable; recovers symbols,"
            " imports and exports via regex."
        ),
        notes=(
            "Recovers top-level functions, classes, and arrow function"
            " declarations when tree-sitter fails."
        ),
    ),
    AdapterInfo(
        name="sql-alembic",
        status="active",
        kind="data",
        extensions=(".py",),
        parser="Python AST Alembic migration detector",
        emits=("migration_facts", "revises_edges", "table_operations"),
        agent_use=(
            "Detects Alembic migrations and their lineage; adds revision"
            " facts and edges to parent migrations."
        ),
        notes=(
            "Detects migrations by revision/down_revision assignments;"
            " resolves revisions to files by id across repo."
        ),
    ),
    AdapterInfo(
        name="swift-ios",
        status="active",
        kind="project",
        extensions=(".pbxproj", ".entitlements", ".plist"),
        parser="Xcode project, entitlements and plist parsers",
        emits=("xcode_targets", "entitlements", "app_groups", "bundle_ids", "reference_edges"),
        agent_use=(
            "Maps iOS targets, entitlements, app extensions and App Groups for native mobile work."
        ),
        notes=(
            "Parses project.pbxproj (ASCII), .entitlements and Info.plist"
            " with plistlib; resolves references to config files."
        ),
    ),
    AdapterInfo(
        name="generic-text",
        status="active",
        kind="document",
        extensions=("*",),
        parser="safe text metadata and content hints",
        emits=("files", "metadata", "content_hints"),
        agent_use="Fallback coverage for docs, configs, unknown text files, and ranking hints.",
        notes=(
            "Does not store full text by default; sensitive files are skipped by scanner policy."
        ),
    ),
)

PLANNED_ADAPTERS: tuple[AdapterInfo, ...] = ()


def list_adapters(include_planned: bool = True) -> list[dict[str, object]]:
    adapters = list(BUILTIN_ADAPTERS)
    if include_planned:
        adapters += list(PLANNED_ADAPTERS)
    return [a.to_dict() for a in adapters]


def adapter_for_extension(extension: str) -> AdapterInfo:
    ext = extension.lower()
    for adapter in BUILTIN_ADAPTERS:
        if ext in adapter.extensions:
            return adapter
    return BUILTIN_ADAPTERS[-1]
