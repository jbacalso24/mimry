from __future__ import annotations

import json
import os
import shutil
import sqlite3
import uuid
from pathlib import Path

from .graphify_core import GraphifyCore
from .paths import idx_path, now, pointer_file
from .scanner import adapt, scan
from .security import contains_sensitive_data
from .semantic import build_semantic_index
from .state import (
    GENERATION_MANIFEST,
    backup_path,
    atomic_write_json,
    fsync_tree,
    generation_manifest,
    _fsync_directory,
)
from .storage import active_index_pointer, connect, register_root, save_pointer, write_jsonl


def _fault(point: str) -> None:
    """Deterministic subprocess-only crash hook used by recovery tests."""
    if os.environ.get("MIMRY_FAULT_POINT") == point:
        os._exit(91)


def _seed_database(previous: Path, staging: Path) -> None:
    source = previous / "mimry.sqlite"
    if not source.is_file():
        return
    try:
        src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        dst = sqlite3.connect(staging / "mimry.sqlite")
        src.backup(dst)
        dst.close()
        src.close()
    except sqlite3.Error:
        # Index data is rebuildable. A damaged legacy DB must not be copied into
        # a new generation; sidecar corruption is still surfaced on normal reads.
        try:
            (staging / "mimry.sqlite").unlink()
        except FileNotFoundError:
            pass


def _collect(root: Path):
    files = []
    symbols = []
    edges = []
    imports = {}
    exports = {}
    for path in scan(root):
        try:
            file_rec, file_symbols, file_edges, file_imports, file_exports = adapt(path, root)
        except (OSError, UnicodeError, ValueError):
            continue
        if contains_sensitive_data((file_rec, file_symbols, file_edges, file_imports, file_exports)):
            continue
        files.append(file_rec)
        symbols += file_symbols
        edges += file_edges
        if file_imports:
            imports[file_rec["rel_path"]] = file_imports
        if file_exports:
            exports[file_rec["rel_path"]] = file_exports
    return files, symbols, edges, imports, exports


def _remove_tree(path: Path) -> None:
    """Remove a cache tree completely or fail instead of reporting false success."""
    if path.is_symlink():
        path.unlink()
    else:
        shutil.rmtree(path)
    if path.exists() or path.is_symlink():
        raise OSError(f"generation cleanup did not completely remove {path}")


def _pointer_generation(pointer: object, base: Path) -> str | None:
    if not isinstance(pointer, dict):
        return None
    generation_id = pointer.get("generationId")
    index_path = pointer.get("indexPath")
    if not isinstance(generation_id, str) or not generation_id or not isinstance(index_path, str):
        return None
    expected = (base / "generations" / generation_id).resolve(strict=False)
    if Path(index_path).expanduser().resolve(strict=False) != expected:
        return None
    return generation_id


def _cleanup_generations(root: Path, base: Path, current: dict | None = None) -> None:
    """Retain only pointer-current + readable LKG and remove crash leftovers.

    The caller must hold ``base/operation.lock`` exclusively so staging and final generation
    cleanup cannot race publication or a current-root cache wipe.
    """
    generations = base / "generations"
    generations.mkdir(parents=True, exist_ok=True)
    protected: set[str] = set()
    current_id = _pointer_generation(current, base)
    if current_id:
        protected.add(current_id)
    pointer = pointer_file(root)
    for candidate in (pointer, backup_path(pointer)):
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        generation_id = _pointer_generation(payload, base)
        if generation_id:
            protected.add(generation_id)

    for child in list(generations.iterdir()):
        if child.name.startswith(".") and child.name.endswith(".staging"):
            _remove_tree(child)
        elif child.name not in protected:
            if child.is_dir() or child.is_symlink():
                _remove_tree(child)
            else:
                child.unlink()


def write_index(root, ptr):
    root = Path(root)
    base = idx_path(ptr["rootId"])
    generations = base / "generations"
    base.mkdir(parents=True, exist_ok=True)

    # The exclusive operation lock serializes publication/legacy migration and
    # prevents GC from deleting generations retained by shared readers.
    with active_index_pointer(
        root,
        exclusive=True,
        validate=False,
        normalize_stale_index_path=True,
    ) as active:
        if not active or active.get("rootId") != ptr.get("rootId"):
            raise RuntimeError("MIMRY root pointer changed while waiting for the operation lock; retry indexing")
        ptr = active
        _cleanup_generations(root, base, ptr)
        files, symbols, edges, imports, exports = _collect(root)
        graph = GraphifyCore().build_graph(files, symbols, edges)
        generation_id = uuid.uuid4().hex
        indexed_at = now()
        staging = generations / f".{generation_id}.staging"
        final = generations / generation_id
        generations.mkdir(parents=True, exist_ok=True)
        staging.mkdir()
        try:
            previous = Path(ptr["indexPath"])
            _seed_database(previous, staging)
            con = connect(staging)
            with con:
                con.execute("delete from files")
                con.execute("delete from symbols")
                con.execute("delete from files_fts")
                con.execute("delete from semantic_chunks where root_id = ?", (ptr["rootId"],))
                con.execute("delete from semantic_metadata where root_id = ?", (ptr["rootId"],))
                con.execute("delete from index_generation")
                con.execute(
                    "insert into index_generation(generation_id, created_at) values(?,?)",
                    (generation_id, indexed_at),
                )
                for file_rec in files:
                    con.execute(
                        "insert or replace into files values(?,?,?,?,?,?,?,?)",
                        (
                            file_rec["file_id"],
                            file_rec["rel_path"],
                            file_rec["filename"],
                            file_rec["extension"],
                            file_rec["adapter"],
                            file_rec["parse_status"],
                            file_rec["content_hint"],
                            file_rec["metadata_text"],
                        ),
                    )
                    con.execute(
                        "insert into files_fts values(?,?,?,?,?,?)",
                        (
                            file_rec["file_id"],
                            file_rec["rel_path"],
                            file_rec["filename"],
                            file_rec["extension"],
                            file_rec["content_hint"],
                            file_rec["metadata_text"],
                        ),
                    )
                for symbol in symbols:
                    con.execute(
                        "insert or replace into symbols values(?,?,?,?,?,?)",
                        (
                            symbol["symbol_id"],
                            symbol["file_id"],
                            symbol["name"],
                            symbol["kind"],
                            symbol["language"],
                            symbol["line_start"],
                        ),
                    )
            con.close()
            _fault("after-sqlite")

            write_jsonl(staging / "files.jsonl", files)
            write_jsonl(staging / "symbols.jsonl", symbols)
            write_jsonl(staging / "imports.jsonl", [{"file": key, "imports": value} for key, value in imports.items()])
            write_jsonl(staging / "exports.jsonl", [{"file": key, "exports": value} for key, value in exports.items()])
            atomic_write_json(staging / "dependencies.json", imports)
            atomic_write_json(staging / "graph.json", graph)
            atomic_write_json(
                staging / "file-hashes.json",
                {
                    file_rec["rel_path"]: {
                        "hash": file_rec["hash"],
                        "mtime": file_rec["mtime"],
                        "size": file_rec["size"],
                    }
                    for file_rec in files
                },
            )
            semantic = build_semantic_index(staging, ptr["rootId"], generation_id)
            _fault("after-sidecars")

            manifest = generation_manifest(staging, generation_id, indexed_at)
            atomic_write_json(staging / GENERATION_MANIFEST, manifest)
            fsync_tree(staging)
            os.replace(staging, final)
            _fsync_directory(generations)
            _fault("after-generation")

            published = {
                **ptr,
                "indexPath": str(final),
                "generationId": generation_id,
                "lastIndexedAt": indexed_at,
            }
            save_pointer(root, published)
            register_root(published)
            _cleanup_generations(root, base, published)
        except BaseException:
            if staging.exists() or staging.is_symlink():
                _remove_tree(staging)
            raise

    return {
        "files": len(files),
        "symbols": len(symbols),
        "edges": len(edges),
        "index": str(final),
        "generation": generation_id,
        "graph_engine": graph["engine"],
        "semantic_chunks": semantic["chunks"],
        "semantic_backend": semantic["backend"],
    }
