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
        agent_use="Find Python services, tests, functions, classes, imports, and edit locations without reading every file.",
        notes="Currently extracts FunctionDef, AsyncFunctionDef, ClassDef, imports, line_start, line_end.",
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
            "jsx_elements",
            "line_ranges",
        ),
        agent_use="Frontend agents can find React/TypeScript components, functions, classes, imports, JSX usage, and edit locations without reading every TSX file.",
        notes="Extracts functions, classes, variable/arrow components, JSX element references, import/export hints, and line ranges. Call graph is still future work.",
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
        emits=("commands", "package_manager", "frameworks", "entrypoints", "env_names", "agent_rules"),
        agent_use="Gives coding agents exact commands, package managers, frameworks, env variable names, and repo rules before edits without exposing secret values.",
        notes="Operating-context adapter layered alongside Graphify; does not replace code graph extraction.",
    ),
    AdapterInfo(
        name="generic-text",
        status="active",
        kind="document",
        extensions=("*",),
        parser="safe text metadata and content hints",
        emits=("files", "metadata", "content_hints"),
        agent_use="Fallback coverage for docs, configs, unknown text files, and ranking hints.",
        notes="Does not store full text by default; sensitive files are skipped by scanner policy.",
    ),
)

PLANNED_ADAPTERS: tuple[AdapterInfo, ...] = (
    AdapterInfo(
        name="js-ts-regex",
        status="planned",
        kind="code",
        extensions=(".ts", ".tsx", ".js", ".jsx"),
        parser="regex import/export scanner",
        emits=("files", "imports", "exports", "defines_edges"),
        agent_use="Fallback path if tree-sitter is unavailable in a constrained environment.",
        notes="Kept as fallback; active adapter is now typescript-ast.",
    ),
    AdapterInfo(
        name="react-native-expo",
        status="planned",
        kind="project",
        extensions=("app.json", "app.config.ts", "package.json", ".tsx", ".ts"),
        parser="Expo/project manifest + TSX adapter",
        emits=("routes", "screens", "native_modules", "permissions", "app_config_edges"),
        agent_use="Lets mobile agents answer Expo/iOS navigation/config questions without blind CLAUDE.md + file scanning.",
        notes="Use after TypeScript AST baseline.",
    ),
    AdapterInfo(
        name="nextjs-app-router",
        status="planned",
        kind="project",
        extensions=("page.tsx", "layout.tsx", "route.ts", "middleware.ts"),
        parser="filesystem router + TS AST",
        emits=("routes", "layouts", "api_routes", "client_server_boundaries", "route_edges"),
        agent_use="Lets web agents map Otty Control routes/pages/API bridge without reading the whole app.",
        notes="Useful for Otty Control/Maggy web.",
    ),
    AdapterInfo(
        name="fastapi",
        status="planned",
        kind="project",
        extensions=(".py",),
        parser="Python AST with FastAPI decorators",
        emits=("api_routes", "dependencies", "schemas", "service_edges"),
        agent_use="Lets backend agents find endpoints, auth deps, schemas, and service layers quickly.",
        notes="Builds on python-ast.",
    ),
    AdapterInfo(
        name="sql-alembic",
        status="planned",
        kind="data",
        extensions=(".sql", ".py"),
        parser="SQL parser + Alembic migration detector",
        emits=("tables", "columns", "migration_edges", "schema_versions"),
        agent_use="Stops migrations from polluting edit-intent context while still exposing schema history when asked.",
        notes="Important for Maggy backend DB work.",
    ),
    AdapterInfo(
        name="markdown-docs",
        status="planned",
        kind="document",
        extensions=(".md", ".mdx"),
        parser="Markdown heading/frontmatter/link parser",
        emits=("headings", "doc_links", "decision_records", "spec_edges"),
        agent_use="Turns docs/specs/plans into citable context without drowning code-edit queries.",
        notes="Should support Obsidian-style wiki links later.",
    ),
    AdapterInfo(
        name="swift-ios",
        status="planned",
        kind="code",
        extensions=(".swift", ".xcodeproj", ".pbxproj"),
        parser="SwiftSyntax or pragmatic Swift/Xcode parser",
        emits=("targets", "entitlements", "app_groups", "native_symbols", "extension_edges"),
        agent_use="Needed for Maggy iOS share extension and App Group work.",
        notes="Later, but important for mobile native boundaries.",
    ),
)


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
