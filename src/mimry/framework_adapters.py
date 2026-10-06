from __future__ import annotations

import ast
import json
import plistlib
import re
from pathlib import Path
from typing import Any

from .config_manifest_adapter import extract_config_metadata, is_config_manifest
from .paths import stable_id
from .xcode import extract_xcode_targets, parse_pbxproj

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}
_NEXT_ROUTE_FILES = {"page", "layout", "route", "loading", "error", "not-found"}
_TS_EXTS = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}
_SQL_TABLE_RE = re.compile(
    r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`\[]?(?P<name>[A-Za-z_][\w.]*)[\"`\]]?\s*\((?P<body>.*?)\)",
    re.IGNORECASE | re.DOTALL,
)
_WIKI_LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
_MD_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+\.mdx?(?:#[^)]+)?)\)", re.IGNORECASE)
_MD_LINK_TARGET_RE = re.compile(r"\[[^\]]+\]\(([^)]+?)(?:#[^)]+)?\)", re.IGNORECASE)
_SQL_TABLE_REF_RE = re.compile(
    r"\b(?:FROM|JOIN|UPDATE|INSERT\s+INTO|DELETE\s+FROM)\s+[\"`\[]?([A-Za-z_][\w.]*)",
    re.IGNORECASE,
)
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(?P<body>.*?)\n---\s*\n", re.DOTALL)


def enrich_framework_facts(
    path: Path,
    root: Path,
    file_record: dict[str, Any],
    symbols: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    source_data: bytes | None = None,
) -> dict[str, Any]:
    """Attach deterministic framework facts to an indexed file record.

    The structured facts live in metadata_text/content_hint so existing
    FTS, ranking, context packs, and MCP outputs can use them without a
    new storage migration.

    Pass `source_data` (bytes) to avoid reopening the file during
    indexing. If source_data is None, will read from path (for
    non-indexing use).
    """
    rel = path.relative_to(root).as_posix()
    ext = path.suffix.lower()
    facts: list[str] = []
    adapters: list[str] = []
    # Decode source_data once if provided
    source_text = source_data.decode("utf-8", errors="ignore") if source_data else None

    if ext in _TS_EXTS:
        next_facts = nextjs_app_router_facts(rel)
        if next_facts:
            adapters.append("nextjs-app-router")
            facts.extend(next_facts)
        expo_facts = expo_router_facts(rel)
        if expo_facts:
            adapters.append("react-native-expo")
            facts.extend(expo_facts)

    if ext == ".py":
        fastapi_facts = fastapi_endpoint_facts(
            path, root, file_record, symbols, edges, source=source_text
        )
        if fastapi_facts:
            adapters.append("fastapi")
            facts.extend(fastapi_facts)
        sql_facts = sql_schema_facts_from_text(
            source_text or _read_text(path), path, root, file_record, symbols, edges
        )
        if sql_facts:
            adapters.append("sql-schema")
            facts.extend(sql_facts)
        alembic_facts = alembic_migration_facts(
            source_text or _read_text(path), path, root, file_record
        )
        if alembic_facts:
            adapters.append("sql-alembic")
            facts.extend(alembic_facts)

    if path.name in {"app.json", "app.config.json"} or path.name.startswith("app.config."):
        expo_facts = expo_config_facts(path, root, text=source_text)
        if expo_facts:
            adapters.append("react-native-expo")
            facts.extend(expo_facts)
            if is_config_manifest(path):
                facts.insert(0, extract_config_metadata(path, root, data=source_data))

    if ext == ".sql":
        sql_facts = sql_schema_facts_from_text(
            source_text or _read_text(path), path, root, file_record, symbols, edges
        )
        adapters.append("sql-schema")
        facts.extend(sql_facts or ["sql schema file"])

    if ext == ".pbxproj":
        pbxproj_result = pbxproj_facts(source_text or _read_text(path), path, root, file_record)
        if pbxproj_result:
            adapters.append("swift-ios")
            facts.extend(pbxproj_result)

    if ext in {".entitlements", ".plist"}:
        plist_result = plist_facts(path, root, file_record, data=source_data)
        if plist_result:
            adapters.append("swift-ios")
            facts.extend(plist_result)

    if ext in {".md", ".mdx"}:
        doc_facts = markdown_doc_facts(path, root, text=source_text)
        if doc_facts:
            adapters.append("markdown-docs")
            facts.extend(doc_facts)
            if is_config_manifest(path):
                facts.insert(0, extract_config_metadata(path, root, data=source_data))

    if not facts:
        return file_record

    primary = adapters[0] if adapters else file_record["adapter"]
    if file_record["adapter"] not in {"generic", primary}:
        primary = f"{primary}+{file_record['adapter']}"
    file_record["adapter"] = primary
    addition = " | ".join(facts)
    file_record["metadata_text"] = _join_text(file_record.get("metadata_text", ""), addition)
    file_record["content_hint"] = _join_text(
        file_record.get("content_hint", ""), addition, limit=2000
    )
    return file_record


def nextjs_app_router_facts(rel: str) -> list[str]:
    parts = rel.split("/")
    if "app" not in parts:
        return []
    app_index = parts.index("app")
    filename = parts[-1]
    stem = Path(filename).stem
    if stem not in _NEXT_ROUTE_FILES:
        return []
    route_parts = parts[app_index + 1 : -1]
    route = _route_from_segments(route_parts)
    kind = "api" if stem == "route" else stem
    facts = [f"nextjs app router route {route} kind {kind} file {rel}"]
    dynamic_segments = [seg for seg in route_parts if seg.startswith("[") and seg.endswith("]")]
    if dynamic_segments:
        facts.append("nextjs dynamic route segments " + " ".join(dynamic_segments))
    if kind == "api":
        facts.append(f"nextjs api route {route}")
    facts.append(
        "verification hint nextjs use package scripts: build lint typecheck test when present"
    )
    return facts


def _route_from_segments(segments: list[str]) -> str:
    route_segments = []
    for segment in segments:
        if not segment or segment.startswith("(") and segment.endswith(")"):
            continue
        if segment.startswith("@"):
            continue
        if segment.startswith("[[...") and segment.endswith("]]"):
            route_segments.append(f"*{segment[5:-2]}")
        elif segment.startswith("[...") and segment.endswith("]"):
            route_segments.append(f"*{segment[4:-1]}")
        elif segment.startswith("[") and segment.endswith("]"):
            route_segments.append(f":{segment[1:-1]}")
        else:
            route_segments.append(segment)
    return "/" + "/".join(route_segments) if route_segments else "/"


def fastapi_endpoint_facts(
    path: Path,
    root: Path,
    file_record: dict[str, Any],
    symbols: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    source: str | None = None,
) -> list[str]:
    if source is None:
        source = _read_text(path)
    if (
        "FastAPI" not in source
        and "APIRouter" not in source
        and "@app." not in source
        and "@router." not in source
    ):
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    app_names: set[str] = set()
    router_names: set[str] = {"router"}
    facts: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            callee = _ast_call_name(node.value.func)
            if callee.endswith("FastAPI"):
                app_names.update(t.id for t in node.targets if isinstance(t, ast.Name))
                facts.append("fastapi app instance " + " ".join(sorted(app_names)))
            elif callee.endswith("APIRouter"):
                router_names.update(t.id for t in node.targets if isinstance(t, ast.Name))
                facts.append("fastapi router instance " + " ".join(sorted(router_names)))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            route = _fastapi_decorator(deco, app_names | router_names | {"app", "router"})
            if not route:
                continue
            method, route_path = route
            facts.append(
                f"fastapi endpoint {method.upper()} {route_path} function {node.name} file"
                f" {path.relative_to(root).as_posix()}"
            )
            _add_framework_symbol(
                file_record,
                symbols,
                edges,
                node.name,
                "fastapi_endpoint",
                "python",
                getattr(node, "lineno", None),
                getattr(node, "end_lineno", None),
            )
    return sorted(set(facts))


def _fastapi_decorator(node: ast.AST, owners: set[str]) -> tuple[str, str] | None:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    method = node.func.attr.lower()
    if method not in _HTTP_METHODS:
        return None
    owner = _ast_call_name(node.func.value)
    if owner not in owners:
        return None
    if (
        not node.args
        or not isinstance(node.args[0], ast.Constant)
        or not isinstance(node.args[0].value, str)
    ):
        return None
    return method, node.args[0].value


def _ast_call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _ast_call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def expo_config_facts(path: Path, root: Path, text: str | None = None) -> list[str]:
    if text is None:
        text = _read_text(path)
    rel = path.relative_to(root).as_posix()
    facts = [f"expo config file {rel}", "react native mobile surface"]
    if path.name.endswith(".json"):
        try:
            data = json.loads(text or "{}")
        except json.JSONDecodeError:
            return facts + ["expo config parse error"]
        expo = data.get("expo") if isinstance(data, dict) else {}
        if isinstance(expo, dict):
            for key in ("name", "slug", "scheme", "entryPoint"):
                value = expo.get(key)
                if isinstance(value, str):
                    facts.append(f"expo {key} {value}")
            for platform in ("ios", "android"):
                if isinstance(expo.get(platform), dict):
                    facts.append(f"expo native config {platform}")
    elif "expo" in text.lower():
        facts.append("expo app config code")
    return facts


def expo_router_facts(rel: str) -> list[str]:
    parts = rel.split("/")
    if parts[0] != "app" and not (len(parts) > 1 and parts[0] == "src" and parts[1] == "app"):
        if "/screens/" in f"/{rel}" or Path(rel).stem.endswith("Screen"):
            return [f"react native screen {Path(rel).stem} file {rel}"]
        return []
    app_index = 1 if parts[0] == "src" else 0
    stem = Path(parts[-1]).stem
    if stem.startswith("_") or stem in {"layout", "+html", "+not-found"}:
        kind = "layout" if "layout" in stem else "special"
    else:
        kind = "screen"
    route_parts = parts[app_index + 1 : -1]
    if stem != "index":
        route_parts.append(stem)
    route = _route_from_segments(route_parts)
    return [
        f"expo router route {route} kind {kind} screen {Path(rel).stem} file {rel}",
        "react native mobile surface",
    ]


def sql_schema_facts_from_text(
    text: str,
    path: Path,
    root: Path,
    file_record: dict[str, Any],
    symbols: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> list[str]:
    facts: list[str] = []
    for match in _SQL_TABLE_RE.finditer(text):
        table = match.group("name").split(".")[-1]
        columns = _sql_columns(match.group("body"))
        rel = path.relative_to(root).as_posix()
        column_text = " columns " + " ".join(columns) if columns else ""
        facts.append(f"sql schema table {table}{column_text} file {rel}")
        _add_framework_symbol(
            file_record,
            symbols,
            edges,
            table,
            "sql_table",
            "sql",
            _line_for_offset(text, match.start()),
            _line_for_offset(text, match.end()),
        )
    return facts


def _sql_columns(body: str) -> list[str]:
    columns: list[str] = []
    for raw in body.split(","):
        part = raw.strip()
        if not part:
            continue
        first = part.split()[0].strip('"`[]')
        if first.upper() in {"PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT", "KEY"}:
            continue
        if re.match(r"^[A-Za-z_][\w]*$", first) and first not in columns:
            columns.append(first)
    return columns[:40]


def markdown_doc_facts(path: Path, root: Path, text: str | None = None) -> list[str]:
    if text is None:
        text = _read_text(path)
    rel = path.relative_to(root).as_posix()
    facts = [f"markdown doc file {rel}"]
    frontmatter = _frontmatter_facts(text)
    if frontmatter:
        facts.append("markdown frontmatter " + " ".join(frontmatter))
    headings = []
    for line in text.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            headings.append(match.group(2).strip().strip("#"))
        if len(headings) >= 12:
            break
    if headings:
        facts.append("markdown headings " + " | ".join(headings))
    wiki_links = sorted(set(_WIKI_LINK_RE.findall(text)))[:20]
    md_links = sorted(set(_MD_LINK_RE.findall(text)))[:20]
    if wiki_links:
        facts.append("markdown wiki links " + " ".join(wiki_links))
    if md_links:
        facts.append("markdown doc links " + " ".join(md_links))
    lower = rel.lower()
    if any(token in lower for token in ("decision", "adr", "spec", "plan", "readme", "docs/")):
        facts.append("markdown relationship hint docs specs plans decisions")
    return facts


def markdown_link_targets(path: Path, root: Path, source: str | None = None) -> list[str]:
    """Return raw link targets found in a markdown file: md links and
    wiki links.

    Filters out external URLs (http://, https://, etc.) and same-page
    anchors. Returns sorted, de-duplicated list, capped at 50.

    Pass `source` (decoded text) to avoid reopening the file during
    indexing. If source is None, will read from path (for non-indexing
    use).
    """
    text = source if source is not None else _read_text(path)
    targets = set()

    # Find markdown links [text](target) - any target, not just .md/.mdx
    for match in _MD_LINK_TARGET_RE.finditer(text):
        target = match.group(1).strip()
        # Skip external URLs
        if re.match(r"^[a-z][a-z0-9+.-]*://", target, re.IGNORECASE):
            continue
        # Skip mailto: links
        if target.startswith("mailto:"):
            continue
        # Skip same-page anchors
        if target.startswith("#"):
            continue
        # Strip trailing fragment
        if "#" in target:
            target = target.split("#", 1)[0]
        if target:
            targets.add(target)

    # Find wiki links [[target]]
    for match in _WIKI_LINK_RE.finditer(text):
        target = match.group(1).strip()
        if target:
            targets.add(target)

    result = sorted(targets)
    return result[:50]


def sql_table_references(text: str) -> list[str]:
    """Return table names a source file appears to query.

    Scans for SQL DML/DDL keywords followed by an identifier.
    Rules:
    - Take the last dotted segment (public.sessions -> sessions)
    - Strip surrounding quotes/backticks/brackets
    - Ignore SQL keywords that follow (SELECT, WHERE, SET, VALUES, ON,
      AS, INTO)
    - Return sorted, de-duplicated, capped at 50
    """
    tables = set()
    keywords_to_skip = {"SELECT", "WHERE", "SET", "VALUES", "ON", "AS", "INTO"}

    for match in _SQL_TABLE_REF_RE.finditer(text):
        # Get the matched table identifier
        identifier = match.group(1).strip()

        # Strip surrounding quotes/backticks/brackets
        identifier = identifier.strip('"`[]')

        # Take the last dotted segment
        if "." in identifier:
            identifier = identifier.split(".")[-1]

        # Check if this looks like a valid identifier, not a SQL keyword
        if identifier and identifier.upper() not in keywords_to_skip:
            tables.add(identifier.lower())

    result = sorted(tables)
    return result[:50]


def _frontmatter_facts(text: str) -> list[str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return []
    facts: list[str] = []
    for line in match.group("body").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip().strip("\"'")
        if key in {"title", "type", "tags"} and value:
            facts.append(f"{key} {value[:120]}")
    return facts


def _add_framework_symbol(
    file_record: dict[str, Any],
    symbols: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    name: str,
    kind: str,
    language: str,
    line_start: int | None,
    line_end: int | None,
) -> None:
    sid = stable_id(file_record["file_id"], name, kind, line_start or 0)
    if any(s.get("symbol_id") == sid for s in symbols):
        return
    symbols.append(
        {
            "symbol_id": sid,
            "file_id": file_record["file_id"],
            "name": name,
            "kind": kind,
            "language": language,
            "exported": False,
            "line_start": line_start,
            "line_end": line_end,
        }
    )
    edges.append(
        {
            "edge_id": stable_id(file_record["file_id"], sid, "defines"),
            "source_type": "file",
            "source_id": file_record["file_id"],
            "target_type": "symbol",
            "target_id": sid,
            "edge_type": "defines",
            "confidence": 0.9,
        }
    )


def _line_for_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, max(offset, 0)) + 1


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _join_text(existing: str, addition: str, *, limit: int | None = None) -> str:
    # Readers split facts on " | ". The line break keeps output
    # redaction from reading the addition as the value of an assignment
    # on the hint's last line.
    text = "\n | ".join(part for part in (existing, addition) if part)
    return text[:limit] if limit else text


def alembic_migration(source: str) -> dict | None:
    """Parse Alembic migration file and return structured data.

    Returns dict with revision, down_revisions, branch_labels,
    depends_on, and tables, or None if not an Alembic migration.
    """
    if "alembic" not in source:
        return None

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    has_import = False
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "alembic" or alias.name.startswith("alembic."):
                        has_import = True
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "alembic" or node.module.startswith("alembic."):
                    has_import = True

    if not has_import:
        return None

    revision_id, down_revisions, branch_labels, depends_on = _extract_alembic_vars(tree)
    if not revision_id:
        return None

    tables = _extract_alembic_tables(tree)

    return {
        "revision": revision_id,
        "down_revisions": sorted(down_revisions),
        "branch_labels": sorted(branch_labels),
        "depends_on": sorted(depends_on),
        "tables": sorted(tables),
    }


def _extract_list_values(node: ast.AST) -> list[str]:
    """Extract string values from an AST node."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return [node.value]
        elif node.value is None:
            return []
    elif isinstance(node, (ast.Tuple, ast.List)):
        values = []
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                values.append(elt.value)
        return values
    return []


def _extract_alembic_vars(
    tree: ast.Module,
) -> tuple[str | None, list[str], list[str], list[str]]:
    """Extract revision, down_revision, branch_labels, depends_on."""
    revision_id = None
    down_revisions: list[str] = []
    branch_labels: list[str] = []
    depends_on: list[str] = []

    for node in tree.body:
        var_name = None
        value = None

        if isinstance(node, ast.Assign):
            targets = node.targets
            if targets and isinstance(targets[0], ast.Name):
                var_name = targets[0].id
                value = node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                var_name = node.target.id
                value = node.value

        if var_name is None:
            continue

        if var_name == "revision" and isinstance(value, ast.Constant):
            if isinstance(value.value, str):
                revision_id = value.value
        elif var_name == "down_revision" and value:
            down_revisions = _extract_list_values(value)
        elif var_name == "branch_labels" and value:
            branch_labels = _extract_list_values(value)
        elif var_name == "depends_on" and value:
            depends_on = _extract_list_values(value)

    return revision_id, down_revisions, branch_labels, depends_on


def _extract_alembic_tables(tree: ast.Module) -> set[str]:
    """Extract table names from op.* calls in migration functions."""
    tables: set[str] = set()

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Call):
                    func_name = _ast_call_name(stmt.func)
                    if func_name and func_name.split(".")[0] in {"op", "batch_op"}:
                        op_type = func_name.split(".")[-1]
                        if op_type in {
                            "create_table",
                            "drop_table",
                            "add_column",
                            "drop_column",
                            "alter_column",
                            "rename_table",
                            "create_index",
                        }:
                            table_name = None
                            if op_type == "create_index":
                                # create_index table is 2nd arg
                                if len(stmt.args) > 1 and isinstance(stmt.args[1], ast.Constant):
                                    table_name = stmt.args[1].value
                            else:
                                # other ops table is 1st arg
                                if stmt.args and isinstance(stmt.args[0], ast.Constant):
                                    table_name = stmt.args[0].value
                            # Check for table_name keyword argument
                            for kw in stmt.keywords:
                                if kw.arg == "table_name" and isinstance(kw.value, ast.Constant):
                                    table_name = kw.value.value
                            if isinstance(table_name, str):
                                tables.add(table_name)

    return tables


def alembic_migration_facts(
    text: str, path: Path, root: Path, file_record: dict[str, Any]
) -> list[str]:
    """Extract Alembic migration facts from a Python file."""
    migration = alembic_migration(text)
    if not migration:
        return []

    facts: list[str] = []
    rev = migration["revision"]
    facts.append(f"alembic revision {rev}")
    if migration["down_revisions"]:
        facts.append("alembic down revisions " + " ".join(migration["down_revisions"]))
    if migration["branch_labels"]:
        facts.append("alembic branch labels " + " ".join(migration["branch_labels"]))
    if migration["depends_on"]:
        facts.append("alembic depends on " + " ".join(migration["depends_on"]))
    if migration["tables"]:
        facts.append("alembic tables " + " ".join(migration["tables"]))
    return facts


def pbxproj_facts(text: str, path: Path, root: Path, file_record: dict[str, Any]) -> list[str]:
    """Extract Xcode project facts from a pbxproj file."""
    pbxproj_dict = parse_pbxproj(text)
    if not pbxproj_dict:
        return []

    facts: list[str] = []
    targets = extract_xcode_targets(pbxproj_dict)

    for target in targets:
        name = target.get("name", "")
        product_type = target.get("productType", "")
        if name:
            type_str = product_type.split(".")[-1] if product_type else "unknown"
            facts.append(f"xcode target {name} type {type_str}")
        if target.get("bundleId"):
            facts.append(f"xcode bundle id {target['bundleId']} target {name}")
        if target.get("entitlements"):
            facts.append(f"xcode entitlements {target['entitlements']} target {name}")
        if target.get("infoPlist"):
            facts.append(f"xcode info plist {target['infoPlist']} target {name}")

    return sorted(set(facts)) if facts else []


def plist_facts(
    path: Path,
    root: Path,
    file_record: dict[str, Any],
    text: str | None = None,
    data: bytes | None = None,
) -> list[str]:
    """Extract facts from .entitlements and Info.plist files.

    Pass `data` (bytes) to avoid reopening the file during indexing.
    If data is None, will read from path (for non-indexing use).
    """
    import xml.parsers.expat

    # Use provided bytes, or read from path
    if data is None:
        if text is not None:
            data = text.encode("utf-8")
        else:
            try:
                data = path.read_bytes()
            except OSError:
                return []

    # Enforce 1 MB cap
    if len(data) > 1_000_000:
        return []

    # Check for unsafe XML: DOCTYPE with internal subset or <!ENTITY
    upper = data.replace(b"\x00", b"").upper()
    if b"<!ENTITY" in upper or re.search(rb"<!DOCTYPE[^>\[]*\[", upper):
        return []

    facts: list[str] = []

    try:
        plist_data = plistlib.loads(data)
    except (plistlib.InvalidFileException, ValueError, xml.parsers.expat.ExpatError):
        return []

    if not isinstance(plist_data, dict):
        return []

    # Check if this is an Info.plist
    is_info_plist = path.name == "Info.plist" or path.name.endswith("-Info.plist")

    if path.suffix == ".plist":
        if is_info_plist:
            bundle_id = plist_data.get("CFBundleIdentifier")
            if isinstance(bundle_id, str):
                facts.append(f"plist bundle identifier {bundle_id}")
            extensions = plist_data.get("NSExtension")
            if isinstance(extensions, dict):
                point_id = extensions.get("NSExtensionPointIdentifier")
                if isinstance(point_id, str):
                    ext_name = point_id.split(".")[-1] if "." in point_id else point_id
                    facts.append(f"plist app extension {ext_name}")
    elif path.suffix == ".entitlements":
        app_groups = plist_data.get("com.apple.security.application-groups")
        if isinstance(app_groups, (list, tuple)) and app_groups:
            groups_str = " ".join(str(g) for g in app_groups)
            facts.append(f"entitlements app groups {groups_str}")
        keychain_groups = plist_data.get("keychain-access-groups")
        if isinstance(keychain_groups, (list, tuple)) and keychain_groups:
            kg_str = " ".join(str(g) for g in keychain_groups)
            facts.append(f"entitlements keychain groups {kg_str}")
        associated_domains = plist_data.get("com.apple.developer.associated-domains")
        if isinstance(associated_domains, (list, tuple)) and associated_domains:
            ad_str = " ".join(str(d) for d in associated_domains)
            facts.append(f"entitlements associated domains {ad_str}")
        aps_env = plist_data.get("aps-environment")
        if isinstance(aps_env, str):
            facts.append(f"entitlements aps-environment {aps_env}")
        icloud = plist_data.get("com.apple.developer.icloud-container-identifiers")
        if isinstance(icloud, (list, tuple)) and icloud:
            ic_str = " ".join(str(c) for c in icloud)
            facts.append(f"entitlements icloud containers {ic_str}")

    return sorted(set(facts)) if facts else []
