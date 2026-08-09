from __future__ import annotations

import ast
import hashlib
from pathlib import Path

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


def _identity(st) -> tuple:
    """The parts of a stat result that change when a file's content changes."""
    return (st.st_size, st.st_mtime_ns, st.st_ino, st.st_dev)


def read_snapshot(path, limit=1_000_000):
    """Read a file once and prove it did not change underneath us.

    Returns ``(data, stat_result)``. A file can change between stat, read,
    parse, and hash; combining metadata from one version with content from
    another persists evidence that never existed. Stat before, read once, compute
    hash, re-read to verify hash, stat after, and only accept when both metadata
    and content hash are unchanged.

    Metadata equality alone is NOT proof that content did not change: a same-size
    rewrite on the same inode with mtime restored passes metadata checks but has
    different bytes. Hash verification is the authoritative check.

    Raises FileChangedError after bounded retries, so the caller marks the file
    unindexable rather than recording a mixed-version record.
    """
    path = Path(path)
    for attempt in range(3):
        before = path.stat()
        with path.open("rb") as fh:
            data = fh.read(limit)
        # Hash the first read
        first_hash = hashlib.sha256(data).hexdigest()
        # Re-read to verify content didn't change
        with path.open("rb") as fh:
            data_verify = fh.read(limit)
        second_hash = hashlib.sha256(data_verify).hexdigest()
        after = path.stat()

        # Both identity and content hash must match
        if _identity(before) == _identity(after) and first_hash == second_hash:
            return data, before
        if attempt == 2:
            if first_hash != second_hash:
                raise FileChangedError(
                    f"{path} kept changing while MIMRY was reading it (content changed); not indexed this run"
                )
            raise FileChangedError(
                f"{path} kept changing while MIMRY was reading it (metadata changed); not indexed this run"
            )
    raise FileChangedError(f"{path} changed while MIMRY was reading it")


def verify_unchanged(path, st) -> None:
    """Re-stat after every adapter has run and fail closed if the file moved on.

    Adapters below this one legitimately re-open the file for their own
    purposes. Rather than thread the bytes through every one of them, prove at
    the end that they all saw the same version the hash was taken from.
    """
    if _identity(Path(path).stat()) != _identity(st):
        raise FileChangedError(f"{path} changed while MIMRY was parsing it; not indexed this run")


def text_hint(path, limit=12000, data=None):
    if not is_text(path):
        return ""
    try:
        raw = path.read_bytes() if data is None else data
    except OSError:
        return ""
    text = raw[:limit].decode("utf-8", errors="ignore")
    return " ".join([line.strip() for line in text.splitlines() if line.strip()][:40])[:2000]


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


def file_record(path, root, adapter, status, hint, snapshot=None):
    """Build a file record from one consistent snapshot.

    ``snapshot`` is the ``(data, stat)`` pair from read_snapshot. size, mtime,
    and hash all come from it, so they can never describe different versions of
    the file. Callers outside adapt() may omit it and take their own.
    """
    data, st = read_snapshot(path) if snapshot is None else snapshot
    rel = canonical_rel_path(path, root)
    fid = stable_id(SCHEMA_VERSION, rel)
    # Extract filename and extension from canonical path (NFC-normalized)
    canonical_filename = Path(rel).name
    canonical_extension = Path(rel).suffix.lower()
    return {
        "file_id": fid,
        "path": str(path.resolve()),
        "rel_path": rel,
        "filename": canonical_filename,
        "extension": canonical_extension,
        "size": st.st_size,
        "mtime": st.st_mtime,
        # Hashed from the same bytes the parsers below are handed.
        "hash": hashlib.sha256(data).hexdigest(),
        "adapter": adapter,
        "parse_status": status,
        "content_hint": hint,
        "metadata_text": f"{rel} {canonical_filename} {canonical_extension} {hint}",
    }


def adapt(path, root):
    ext = path.suffix.lower()
    # One read for the whole adapter chain. Everything below derives from these
    # bytes, and verify_unchanged() at the end proves nothing shifted meanwhile.
    snapshot = read_snapshot(path)
    data, _st = snapshot
    # Check sensitivity using the captured bytes (not a separate file read)
    # to ensure hash and security classification derive from the same content.
    if has_sensitive_content(path, data=data):
        raise ValueError("secret-bearing content is not indexable")
    hint = safe_hint(path, data=data) if is_config_manifest(path) else text_hint(path, data=data)
    f = file_record(path, root, "generic", "ok", hint, snapshot)
    symbols = []
    edges = []
    imports = []
    exports = []
    calls = []
    inherits = []
    references = {"doc_links": [], "table_refs": []}
    if is_config_manifest(path):
        metadata = extract_config_metadata(path, root, data=data)
        f = file_record(path, root, "config-manifest", "ok", hint, snapshot)
        f["metadata_text"] = metadata
    elif ext == ".py":
        f = file_record(path, root, "python-ast", "ok", hint, snapshot)
        try:
            source = data.decode("utf-8", errors="ignore")
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
            f = file_record(path, root, "python-ast", f"parse_error:{e.__class__.__name__}", hint, snapshot)
    elif ext in {".js", ".jsx", ".ts", ".tsx"}:
        f = file_record(path, root, "typescript-ast", "ok", hint, snapshot)
        source = data.decode("utf-8", errors="ignore")
        symbols, edges, imports, exports, status = parse_ts_like(path, root, f, source=source)
        result = extract_language(path, source)
        calls = result.get("calls", [])
        inherits = result.get("inherits", [])
        f = file_record(path, root, "typescript-ast", status, hint, snapshot)
    elif is_document(path):
        # The path is still needed for the extension check; only the BYTES
        # come from the snapshot. Dropping it made every Office document
        # return parse_error:unsupported_extension.
        text, status = extract_document_text(path, data=data)
        safe_text = redact_sensitive_text(text) if text else ""
        doc_hint = safe_text[:2000]
        f = file_record(path, root, "office-document", status, doc_hint, snapshot)
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
                source = data.decode("utf-8", errors="ignore")
                result = extract_language(path, source)
                if result.get("status") == "ok":
                    f = file_record(path, root, f"tree-sitter-{language}", "ok", hint, snapshot)
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
                    f = file_record(
                        path, root, f"tree-sitter-{language}", result.get("status", "parse_error"), hint, snapshot
                    )
            except (OSError, UnicodeError):
                pass
    # Populate references for markdown and SQL
    if ext in {".md", ".mdx"}:
        source = data.decode("utf-8", errors="ignore")
        doc_links = markdown_link_targets(path, root, source=source)
        if doc_links:
            references["doc_links"] = doc_links

    # Populate table_refs for any text file (including .sql)
    if ext not in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".bin", ".o", ".exe", ".dll", ".so"}:
        try:
            source = data.decode("utf-8", errors="ignore")
            table_refs = sql_table_references(source)
            if table_refs:
                references["table_refs"] = table_refs
        except (OSError, UnicodeError):
            pass

    if inherits:
        references["inherits"] = inherits

    f = enrich_framework_facts(path, root, f, symbols, edges, source_data=data)
    verify_unchanged(path, _st)
    if contains_sensitive_data((f, symbols, edges, imports, exports, calls, references)):
        raise ValueError("adapter output contained sensitive data")
    return f, symbols, edges, sorted(set(imports)), sorted(set(exports)), calls, references
