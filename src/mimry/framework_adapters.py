from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from .config_manifest_adapter import extract_config_metadata, is_config_manifest
from .paths import stable_id

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}
_NEXT_ROUTE_FILES = {"page", "layout", "route", "loading", "error", "not-found"}
_TS_EXTS = {".js", ".jsx", ".ts", ".tsx"}
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
) -> dict[str, Any]:
    """Attach deterministic framework facts to an indexed file record.

    The structured facts live in metadata_text/content_hint so existing FTS, ranking,
    context packs, and MCP outputs can use them without a new storage migration.
    """
    rel = path.relative_to(root).as_posix()
    ext = path.suffix.lower()
    facts: list[str] = []
    adapters: list[str] = []

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
        fastapi_facts = fastapi_endpoint_facts(path, root, file_record, symbols, edges)
        if fastapi_facts:
            adapters.append("fastapi")
            facts.extend(fastapi_facts)
        sql_facts = sql_schema_facts_from_text(_read_text(path), path, root, file_record, symbols, edges)
        if sql_facts:
            adapters.append("sql-schema")
            facts.extend(sql_facts)

    if path.name in {"app.json", "app.config.json"} or path.name.startswith("app.config."):
        expo_facts = expo_config_facts(path, root)
        if expo_facts:
            adapters.append("react-native-expo")
            facts.extend(expo_facts)
            if is_config_manifest(path):
                facts.insert(0, extract_config_metadata(path, root))

    if ext == ".sql":
        sql_facts = sql_schema_facts_from_text(_read_text(path), path, root, file_record, symbols, edges)
        adapters.append("sql-schema")
        facts.extend(sql_facts or ["sql schema file"])

    if ext in {".md", ".mdx"}:
        doc_facts = markdown_doc_facts(path, root)
        if doc_facts:
            adapters.append("markdown-docs")
            facts.extend(doc_facts)
            if is_config_manifest(path):
                facts.insert(0, extract_config_metadata(path, root))

    if not facts:
        return file_record

    primary = adapters[0] if adapters else file_record["adapter"]
    if file_record["adapter"] not in {"generic", primary}:
        primary = f"{primary}+{file_record['adapter']}"
    file_record["adapter"] = primary
    addition = " | ".join(facts)
    file_record["metadata_text"] = _join_text(file_record.get("metadata_text", ""), addition)
    file_record["content_hint"] = _join_text(file_record.get("content_hint", ""), addition, limit=2000)
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
    facts.append("verification hint nextjs use package scripts: build lint typecheck test when present")
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
) -> list[str]:
    source = _read_text(path)
    if "FastAPI" not in source and "APIRouter" not in source and "@app." not in source and "@router." not in source:
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
                f"fastapi endpoint {method.upper()} {route_path} function {node.name} file {path.relative_to(root).as_posix()}"
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
    if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
        return None
    return method, node.args[0].value


def _ast_call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _ast_call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def expo_config_facts(path: Path, root: Path) -> list[str]:
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
    return [f"expo router route {route} kind {kind} screen {Path(rel).stem} file {rel}", "react native mobile surface"]


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


def markdown_doc_facts(path: Path, root: Path) -> list[str]:
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


def markdown_link_targets(path: Path, root: Path) -> list[str]:
    """Return raw link targets found in a markdown file: md links and wiki links.

    Filters out external URLs (http://, https://, etc.) and same-page anchors.
    Returns sorted, de-duplicated list, capped at 50.
    """
    text = _read_text(path)
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
    - Ignore SQL keywords that follow (SELECT, WHERE, SET, VALUES, ON, AS, INTO)
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
    text = " | ".join(part for part in (existing, addition) if part)
    return text[:limit] if limit else text
