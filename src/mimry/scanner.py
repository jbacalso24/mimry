from __future__ import annotations

import ast
import hashlib

from .config_manifest_adapter import extract_config_metadata, is_config_manifest, safe_hint
from .constants import SCHEMA_VERSION
from .core.documents import extract_document_text, is_document
from .core.languages import extract as extract_language
from .core.languages import language_for
from .framework_adapters import enrich_framework_facts, markdown_link_targets, sql_table_references
from .paths import stable_id, canonical_rel_path
from .security import (
    contains_sensitive_data,
    has_sensitive_content,
    is_text,
    redact_sensitive_text,
    safe_root,
    should_ignore,
)
from .ts_ast_adapter import parse_ts_like


class FileChangedError(OSError):
    """Raised when a file changes between snapshots during indexing."""

    pass


def read_snapshot(path, limit=1_000_000):
    """Read a file once and prove it did not change underneath us.

    Returns (data, stat_result). Raises FileChangedError if the file's
    size/mtime/inode changed across the read, after bounded retries.
    """
    from pathlib import Path

    path = Path(path)
    for attempt in range(3):
        stat_before = path.stat()
        try:
            with path.open("rb") as fh:
                data = fh.read(limit + 1)
        except OSError as e:
            raise FileChangedError(f"Cannot read {path}: {e}") from e

        stat_after = path.stat()

        # Check if file changed: size, mtime, or inode (st_ino on POSIX)
        if (
            stat_before.st_size == stat_after.st_size
            and stat_before.st_mtime == stat_after.st_mtime
            and stat_before.st_ino == stat_after.st_ino
        ):
            return data, stat_before

        if attempt < 2:
            continue
        raise FileChangedError(f"File {path} changed between stat checks during read")

    raise FileChangedError(f"File {path} changed during read snapshot")


def text_hint(path, limit=12000):
    if not is_text(path):
        return ""
    try:
        text = path.read_bytes()[:limit].decode("utf-8", errors="ignore")
    except OSError:
        return ""
    return " ".join([line.strip() for line in text.splitlines() if line.strip()][:40])[:2000]


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def scan(root):
    safe_root(root)
    paths = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink() or should_ignore(path, root):
            continue
        try:
            if path.stat().st_size > 1_000_000:
                continue
            # Windows/macOS can expose locked or ACL-protected files as regular
            # files, then fail only when opened for hashing. Treat unreadable
            # files like ignored/generated files; one locked DB sidecar should
            # not abort the whole index refresh.
            with path.open("rb"):
                pass
        except OSError:
            continue
        paths.append(path)
    # Sort deterministically by canonical path to ensure filesystem order doesn't leak into results
    for path in sorted(paths, key=lambda p: canonical_rel_path(p, root)):
        yield path


def file_record(path, root, adapter, status, hint):
    st = path.stat()
    rel = canonical_rel_path(path, root)
    fid = stable_id(SCHEMA_VERSION, rel)
    return {
        "file_id": fid,
        "path": str(path.resolve()),
        "rel_path": rel,
        "filename": path.name,
        "extension": path.suffix.lower(),
        "size": st.st_size,
        "mtime": st.st_mtime,
        "hash": sha(path),
        "adapter": adapter,
        "parse_status": status,
        "content_hint": hint,
        "metadata_text": f"{rel} {path.name} {path.suffix.lower()} {hint}",
    }


def adapt(path, root):
    # Recheck immediately before adapters read content to close the scan/adapt
    # boundary and fail closed if a file changed after discovery.
    if has_sensitive_content(path):
        raise ValueError("secret-bearing content is not indexable")
    ext = path.suffix.lower()
    hint = safe_hint(path) if is_config_manifest(path) else text_hint(path)
    f = file_record(path, root, "generic", "ok", hint)
    symbols = []
    edges = []
    imports = []
    exports = []
    calls = []
    inherits = []
    references = {"doc_links": [], "table_refs": []}
    if is_config_manifest(path):
        metadata = extract_config_metadata(path, root)
        f = file_record(path, root, "config-manifest", "ok", hint)
        f["metadata_text"] = metadata
    elif ext == ".py":
        f = file_record(path, root, "python-ast", "ok", hint)
        try:
            source = path.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    kind = "class" if isinstance(node, ast.ClassDef) else "function"
                    sid = stable_id(f["file_id"], node.name, kind, getattr(node, "lineno", 0))
                    symbols.append(
                        {
                            "symbol_id": sid,
                            "file_id": f["file_id"],
                            "name": node.name,
                            "kind": kind,
                            "language": "python",
                            "exported": False,
                            "line_start": getattr(node, "lineno", None),
                            "line_end": getattr(node, "end_lineno", None),
                        }
                    )
                    edges.append(
                        {
                            "edge_id": stable_id(f["file_id"], sid, "defines"),
                            "source_type": "file",
                            "source_id": f["file_id"],
                            "target_type": "symbol",
                            "target_id": sid,
                            "edge_type": "defines",
                            "confidence": 1.0,
                        }
                    )
                    if isinstance(node, ast.ClassDef):
                        for base in node.bases:
                            base_name = getattr(base, "id", None) or getattr(base, "attr", None)
                            if base_name:
                                inherits.append(
                                    {"type": node.name, "base": base_name, "line": getattr(node, "lineno", None)}
                                )
                elif isinstance(node, ast.Call):
                    callee_name = None
                    if isinstance(node.func, ast.Name):
                        callee_name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        callee_name = node.func.attr
                    if callee_name:
                        calls.append({"name": callee_name, "line": getattr(node, "lineno", None)})
                elif isinstance(node, ast.Import):
                    imports += [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.append("." * node.level + node.module)
        except SyntaxError as e:
            f = file_record(path, root, "python-ast", f"parse_error:{e.__class__.__name__}", hint)
    elif ext in {".js", ".jsx", ".ts", ".tsx"}:
        f = file_record(path, root, "typescript-ast", "ok", hint)
        symbols, edges, imports, exports, status = parse_ts_like(path, root, f)
        source = path.read_text(encoding="utf-8", errors="ignore")
        result = extract_language(path, source)
        calls = result.get("calls", [])
        inherits = result.get("inherits", [])
        f = file_record(path, root, "typescript-ast", status, hint)
    elif is_document(path):
        text, status = extract_document_text(path)
        safe_text = redact_sensitive_text(text) if text else ""
        doc_hint = safe_text[:2000]
        f = file_record(path, root, "office-document", status, doc_hint)
        # Populate table_refs only from the same redacted text allowed into the
        # searchable index and semantic/context surfaces.
        if safe_text:
            table_refs = sql_table_references(safe_text)
            if table_refs:
                references["table_refs"] = table_refs
    else:
        # Go/Rust/C# via core.languages
        language = language_for(path)
        if language and ext not in {".py", ".js", ".jsx", ".ts", ".tsx"}:
            try:
                source = path.read_text(encoding="utf-8", errors="ignore")
                result = extract_language(path, source)
                if result.get("status") == "ok":
                    f = file_record(path, root, f"tree-sitter-{language}", "ok", hint)
                    for d in result.get("definitions", []):
                        sid = stable_id(f["file_id"], d["name"], d["kind"], d["line_start"] or 0)
                        symbols.append(
                            {
                                "symbol_id": sid,
                                "file_id": f["file_id"],
                                "name": d["name"],
                                "kind": d["kind"],
                                "language": language,
                                "exported": d["exported"],
                                "line_start": d["line_start"],
                                "line_end": d["line_end"],
                            }
                        )
                        edges.append(
                            {
                                "edge_id": stable_id(f["file_id"], sid, "defines"),
                                "source_type": "file",
                                "source_id": f["file_id"],
                                "target_type": "symbol",
                                "target_id": sid,
                                "edge_type": "defines",
                                "confidence": 1.0,
                            }
                        )
                    imports = [i["module"] for i in result.get("imports", [])]
                    calls = result.get("calls", [])
                    inherits = result.get("inherits", [])
                else:
                    f = file_record(path, root, f"tree-sitter-{language}", result.get("status", "parse_error"), hint)
            except (OSError, UnicodeError):
                pass
    # Populate references for markdown and SQL
    if ext in {".md", ".mdx"}:
        doc_links = markdown_link_targets(path, root)
        if doc_links:
            references["doc_links"] = doc_links

    # Populate table_refs for any text file (including .sql)
    if ext not in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".bin", ".o", ".exe", ".dll", ".so"}:
        try:
            source = path.read_text(encoding="utf-8", errors="ignore")
            table_refs = sql_table_references(source)
            if table_refs:
                references["table_refs"] = table_refs
        except (OSError, UnicodeError):
            pass

    if inherits:
        references["inherits"] = inherits

    f = enrich_framework_facts(path, root, f, symbols, edges)
    if contains_sensitive_data((f, symbols, edges, imports, exports, calls, references)):
        raise ValueError("adapter output contained sensitive data")
    return f, symbols, edges, sorted(set(imports)), sorted(set(exports)), calls, references
