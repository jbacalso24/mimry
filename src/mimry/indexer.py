from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import uuid
from pathlib import Path

from .core.build import GraphEngine, canonicalize_edges
from .core.cluster import assign_communities
from .core.resolve import (
    resolve_imports,
    resolve_calls,
    resolve_doc_links,
    resolve_inheritance,
    resolve_table_refs,
)
from .core.report import render_report, build_manifest
from .paths import idx_path, now, pointer_file, graph_output_dir
from .constants import SCHEMA_VERSION
from .scanner import adapt, scan, FileChangedError
from .security import contains_sensitive_data
from .semantic import build_semantic_index
from .state import (
    GENERATION_MANIFEST,
    UNINDEXABLE_FILE,
    backup_path,
    atomic_write_json,
    atomic_write_text,
    fsync_tree,
    generation_manifest,
    _fsync_directory,
)
from .storage import active_index_pointer, connect, register_root, save_pointer, write_jsonl


def _fault(point: str) -> None:
    """Deterministic subprocess-only crash hook used by recovery tests."""
    if os.environ.get("MIMRY_FAULT_POINT") == point:
        os._exit(91)


def _git_commit(root: Path) -> str | None:
    """Get the current git commit hash. Returns None on any failure."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


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
    calls = {}
    references = {}
    symbols_by_file = {}
    # Paths deliberately kept out of the index: secret-bearing or unreadable. They are
    # recorded so freshness can tell "MIMRY refused this" from "the user changed this".
    # Without it a single secret-bearing file reports as changed on every run, the index
    # never reaches `current`, and that pins graph health to stale -- which disables
    # `mimry path` entirely. Content never leaves this list; only the path is kept.
    unindexable: list[str] = []

    def _note_unindexable(path: Path) -> None:
        try:
            unindexable.append(path.relative_to(root).as_posix())
        except ValueError:
            pass

    for path in scan(root):
        try:
            file_rec, file_symbols, file_edges, file_imports, file_exports, file_calls, file_references = adapt(
                path, root
            )
        except (FileChangedError, OSError, UnicodeError, ValueError):
            _note_unindexable(path)
            continue
        if contains_sensitive_data(
            (file_rec, file_symbols, file_edges, file_imports, file_exports, file_calls, file_references)
        ):
            _note_unindexable(path)
            continue
        files.append(file_rec)
        symbols += file_symbols
        edges += file_edges
        if file_imports:
            imports[file_rec["rel_path"]] = file_imports
        if file_exports:
            exports[file_rec["rel_path"]] = file_exports
        if file_calls:
            calls[file_rec["rel_path"]] = file_calls
        if file_symbols:
            symbols_by_file[file_rec["rel_path"]] = file_symbols
        # Accumulate references only if there's at least one non-empty list
        if file_references.get("doc_links") or file_references.get("table_refs") or file_references.get("inherits"):
            references[file_rec["rel_path"]] = file_references

    # Sort all collections for determinism
    files = sorted(files, key=lambda f: f["rel_path"])
    symbols = sorted(
        symbols, key=lambda s: (s["file_id"], s["name"], s["kind"], s.get("line_start") or 0, s["symbol_id"])
    )
    edges = sorted(
        edges,
        key=lambda e: (
            e["source_type"],
            e["source_id"],
            e["target_type"],
            e["target_id"],
            e["edge_type"],
            str(e["confidence"]),
            e["edge_id"],
        ),
    )
    imports = dict(sorted(imports.items()))
    exports = dict(sorted(exports.items()))
    calls_items = [
        (k, sorted(v, key=lambda c: (c.get("name", ""), c.get("line") or 0))) for k, v in sorted(calls.items())
    ]
    calls = dict(calls_items)
    symbols_by_file = dict(sorted(symbols_by_file.items()))
    references = dict(sorted(references.items()))

    return files, symbols, edges, imports, exports, calls, symbols_by_file, references, sorted(set(unindexable))


def _edge(source: str, target: str, relation: str, confidence) -> dict:
    return {"source": source, "target": target, "relation": relation, "confidence": confidence}


def _symbol_ids_by_path_name(files, symbols) -> dict:
    """(rel_path, symbol_name) -> symbol_id."""
    rel_path_of = {f["file_id"]: f["rel_path"] for f in files}
    index = {}
    for sym in symbols:
        rel_path = rel_path_of.get(sym["file_id"])
        if rel_path is not None:
            index[(rel_path, sym["name"])] = sym["symbol_id"]
    return index


def _table_symbols(files, symbols) -> dict:
    """lower(table_name) -> [(rel_path, symbol_id), ...]."""
    rel_path_of = {f["file_id"]: f["rel_path"] for f in files}
    tables: dict[str, list] = {}
    for sym in symbols:
        if sym.get("kind") != "sql_table":
            continue
        rel_path = rel_path_of.get(sym["file_id"])
        if rel_path is not None:
            tables.setdefault(sym.get("name", "").lower(), []).append((rel_path, sym["symbol_id"]))
    return tables


def _split_references(references) -> tuple[dict, dict, dict]:
    """Fan the per-file references bag out into one dict per relationship kind."""
    doc_links, table_refs, inherits = {}, {}, {}
    for rel_path, data in (references or {}).items():
        for key, sink in (("doc_links", doc_links), ("table_refs", table_refs), ("inherits", inherits)):
            if data.get(key):
                sink[rel_path] = data[key]
    return doc_links, table_refs, inherits


def _import_graph_edges(import_edges, file_id_of) -> list[dict]:
    """file -> file."""
    edges = []
    for e in import_edges:
        source, target = file_id_of.get(e["importer"]), file_id_of.get(e["target"])
        if source and target:
            edges.append(_edge(f"file:{source}", f"file:{target}", "imports", e["confidence"]))
    return edges


def _call_graph_edges(calls, symbols_by_file, import_edges, symbol_ids) -> list[dict]:
    """symbol -> symbol."""
    edges = []
    for e in resolve_calls(calls, symbols_by_file, import_edges):
        if e.get("caller_symbol") is None or e.get("target_symbol") is None:
            continue
        caller = e.get("caller_symbol_id") or symbol_ids.get((e["caller_file"], e["caller_symbol"]))
        target = e.get("target_symbol_id") or symbol_ids.get((e["target_file"], e["target_symbol"]))
        if caller and target:
            edges.append(_edge(f"symbol:{caller}", f"symbol:{target}", "calls", e["confidence"]))
    return edges


def _inheritance_graph_edges(inherits, symbols_by_file, import_edges, symbol_ids) -> list[dict]:
    """symbol -> symbol, child to base."""
    edges = []
    for e in resolve_inheritance(inherits, symbols_by_file, import_edges):
        child = symbol_ids.get((e["child_file"], e["child_symbol"]))
        base = symbol_ids.get((e["base_file"], e["base_symbol"]))
        if child and base and child != base:
            edges.append(_edge(f"symbol:{child}", f"symbol:{base}", "inherits", e["confidence"]))
    return edges


def _doc_link_graph_edges(doc_links, rel_paths, file_id_of) -> list[dict]:
    """file -> file, from markdown links."""
    edges = []
    for e in resolve_doc_links(doc_links, rel_paths):
        source, target = file_id_of.get(e["source"]), file_id_of.get(e["target"])
        if source and target:
            edges.append(_edge(f"file:{source}", f"file:{target}", "references", e["confidence"]))
    return edges


def _table_ref_graph_edges(table_refs, table_symbols, file_id_of) -> list[dict]:
    """file -> symbol, from SQL table mentions."""
    edges = []
    for e in resolve_table_refs(table_refs, table_symbols):
        source, target = file_id_of.get(e["source"]), e.get("target_symbol_id")
        if source and target:
            edges.append(_edge(f"file:{source}", f"symbol:{target}", "references", e["confidence"]))
    return edges


def _finalize_graph(graph) -> dict:
    """Drop dangling edges, cluster, and sort.

    The sort is not cosmetic: graph.json is checksummed by the generation manifest,
    so an unstable order would break generation coherence.
    """
    node_ids = {n["id"] for n in graph["nodes"]}
    graph["edges"] = [e for e in graph["edges"] if e.get("source") in node_ids and e.get("target") in node_ids]
    # (source, target, relation) is not a total key: equal-endpoint edges that
    # differ only in confidence kept insertion order. canonicalize_edges applies
    # the documented EXTRACTED-over-INFERRED policy and a total sort key.
    graph["edges"] = canonicalize_edges(graph["edges"])
    graph["nodes"] = sorted(assign_communities(graph["nodes"], graph["edges"]), key=lambda n: n["id"])
    return graph


def _build_core_graph(files, symbols, edges, imports, exports, calls, symbols_by_file, references):
    """Assemble the relationship graph: defines + imports + calls + inherits + references."""
    graph = GraphEngine().build_graph(files, symbols, edges, imports=imports, exports=exports)

    rel_paths = {f["rel_path"] for f in files}
    file_id_of = {f["rel_path"]: f["file_id"] for f in files}
    symbol_ids = _symbol_ids_by_path_name(files, symbols)
    import_edges = resolve_imports(imports, rel_paths)
    doc_links, table_refs, inherits = _split_references(references)

    graph["edges"] += _import_graph_edges(import_edges, file_id_of)
    graph["edges"] += _call_graph_edges(calls, symbols_by_file, import_edges, symbol_ids)
    graph["edges"] += _inheritance_graph_edges(inherits, symbols_by_file, import_edges, symbol_ids)
    graph["edges"] += _doc_link_graph_edges(doc_links, rel_paths, file_id_of)
    graph["edges"] += _table_ref_graph_edges(table_refs, _table_symbols(files, symbols), file_id_of)

    return _finalize_graph(graph)


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

        # Validate schema version: if pointer has a recorded schema version and it doesn't match
        # the current SCHEMA_VERSION, we must rebuild to recompute file IDs correctly.
        existing_schema = ptr.get("schemaVersion")
        if existing_schema and existing_schema != SCHEMA_VERSION:
            # Schema version mismatch: invalidate the existing generation and rebuild
            pass  # Will rebuild with the new schema version

        _cleanup_generations(root, base, ptr)
        files, symbols, edges, imports, exports, calls, symbols_by_file, references, unindexable = _collect(root)
        graph = _build_core_graph(files, symbols, edges, imports, exports, calls, symbols_by_file, references)
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
            atomic_write_json(staging / UNINDEXABLE_FILE, unindexable)
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
                "schemaVersion": SCHEMA_VERSION,
            }
            save_pointer(root, published)
            register_root(published)
            _cleanup_generations(root, base, published)

            # Write visible graph artifacts after successful publication
            out = graph_output_dir(root)
            out.mkdir(parents=True, exist_ok=True)
            atomic_write_json(out / "graph.json", graph)
            atomic_write_text(out / "GRAPH_REPORT.md", render_report(graph, commit=_git_commit(root)))
            atomic_write_json(out / "manifest.json", build_manifest(files))
        except BaseException:
            if staging.exists() or staging.is_symlink():
                _remove_tree(staging)
            raise

    return {
        "files": len(files),
        "symbols": len(symbols),
        "edges": len(graph["edges"]),
        "index": str(final),
        "generation": generation_id,
        "graph_engine": graph["engine"],
        "semantic_chunks": semantic["chunks"],
        "semantic_backend": semantic["backend"],
    }
