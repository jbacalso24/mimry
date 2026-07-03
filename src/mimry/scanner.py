from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from .config_manifest_adapter import extract_config_metadata, is_config_manifest, safe_hint
from .paths import stable_id
from .security import is_text, safe_root, should_ignore
from .ts_ast_adapter import parse_ts_like


def text_hint(path, limit=12000):
    if not is_text(path):
        return ""
    try:
        text = path.read_bytes()[:limit].decode("utf-8", errors="ignore")
    except OSError:
        return ""
    return " ".join([l.strip() for l in text.splitlines() if l.strip()][:40])[:2000]


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def scan(root):
    safe_root(root)
    for path in root.rglob("*"):
        if should_ignore(path, root) or not path.is_file() or path.is_symlink():
            continue
        try:
            if path.stat().st_size > 1_000_000:
                continue
        except OSError:
            continue
        yield path


def file_record(path, root, adapter, status, hint):
    st = path.stat()
    rel = path.relative_to(root).as_posix()
    fid = stable_id(str(root.resolve()), rel)
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
    ext = path.suffix.lower()
    hint = safe_hint(path) if is_config_manifest(path) else text_hint(path)
    f = file_record(path, root, "generic", "ok", hint)
    symbols = []
    edges = []
    imports = []
    exports = []
    if is_config_manifest(path):
        metadata = extract_config_metadata(path, root)
        f = file_record(path, root, "config-manifest", "ok", hint)
        f["metadata_text"] = metadata
    elif ext == ".py":
        f = file_record(path, root, "python-ast", "ok", hint)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
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
                elif isinstance(node, ast.Import):
                    imports += [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.append(node.module)
        except SyntaxError as e:
            f = file_record(path, root, "python-ast", f"parse_error:{e.__class__.__name__}", hint)
    elif ext in {".js", ".jsx", ".ts", ".tsx"}:
        f = file_record(path, root, "typescript-ast", "ok", hint)
        symbols, edges, imports, exports, status = parse_ts_like(path, root, f)
        f = file_record(path, root, "typescript-ast", status, hint)
    return f, symbols, edges, sorted(set(imports)), sorted(set(exports))
