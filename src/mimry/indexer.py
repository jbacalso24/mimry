from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import uuid
from pathlib import Path

from .core.build import GraphEngine
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
from .scanner import adapt, scan
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
        except (OSError, UnicodeError, ValueError):
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
    return files, symbols, edges, imports, exports, calls, symbols_by_file, references, sorted(set(unindexable))


def _build_core_graph(files, symbols, edges, imports, exports, calls, symbols_by_file, references):
    """Build the native relationship graph: defines + imports + calls + references, then cluster."""
    # Step 1: build_graph with imports/exports
    graph = GraphEngine().build_graph(files, symbols, edges, imports=imports, exports=exports)

    # Step 2: resolve imports
    rel_paths = {f["rel_path"] for f in files}
    import_edges = resolve_imports(imports, rel_paths)

    # Step 3: convert import edges to graph edges (FILE to FILE)
    file_id_of = {f["rel_path"]: f["file_id"] for f in files}
    for e in import_edges:
        importer_id = file_id_of.get(e["importer"])
        target_id = file_id_of.get(e["target"])
        if importer_id and target_id:
            graph["edges"].append(
                {
                    "source": f"file:{importer_id}",
                    "target": f"file:{target_id}",
                    "relation": "imports",
                    "confidence": e["confidence"],
                }
            )

    # Step 4: resolve calls and convert to graph edges (SYMBOL to SYMBOL)
    call_edges = resolve_calls(calls, symbols_by_file, import_edges)
    rel_path_to_id = {f["rel_path"]: f["file_id"] for f in files}
    # Build (rel_path, symbol_name) -> symbol_id map
    rel_path_of_file_id = {f["file_id"]: f["rel_path"] for f in files}
    symbol_by_path_name = {}
    for sym in symbols:
        rel = rel_path_of_file_id.get(sym["file_id"])
        if rel is not None:
            symbol_by_path_name[(rel, sym["name"])] = sym["symbol_id"]

    for e in call_edges:
        caller_file = e.get("caller_file")
        caller_symbol = e.get("caller_symbol")
        target_file = e.get("target_file")
        target_symbol = e.get("target_symbol")

        # Skip if caller_symbol or target_symbol is None
        if caller_symbol is None or target_symbol is None:
            continue

        # Resolve to symbol ids
        caller_id = symbol_by_path_name.get((caller_file, caller_symbol))
        target_id = symbol_by_path_name.get((target_file, target_symbol))

        if caller_id and target_id:
            graph["edges"].append(
                {
                    "source": f"symbol:{caller_id}",
                    "target": f"symbol:{target_id}",
                    "relation": "calls",
                    "confidence": e["confidence"],
                }
            )

    # Step 5: reconstruct doc_links and table_refs from references
    # References structure: {rel_path: {"doc_links": [...], "table_refs": [...]}}
    doc_links_dict = {}
    table_refs_dict = {}
    inherits_dict = {}
    if references:
        for rel_path, ref_data in references.items():
            if ref_data.get("doc_links"):
                doc_links_dict[rel_path] = ref_data["doc_links"]
            if ref_data.get("table_refs"):
                table_refs_dict[rel_path] = ref_data["table_refs"]
            if ref_data.get("inherits"):
                inherits_dict[rel_path] = ref_data["inherits"]

    # Step 5b: resolve base types and convert to graph edges (SYMBOL to SYMBOL)
    for e in resolve_inheritance(inherits_dict, symbols_by_file, import_edges):
        child_id = symbol_by_path_name.get((e["child_file"], e["child_symbol"]))
        base_id = symbol_by_path_name.get((e["base_file"], e["base_symbol"]))
        if child_id and base_id and child_id != base_id:
            graph["edges"].append(
                {
                    "source": f"symbol:{child_id}",
                    "target": f"symbol:{base_id}",
                    "relation": "inherits",
                    "confidence": e["confidence"],
                }
            )

    # Step 6: resolve doc links and convert to graph edges (FILE to FILE)
    doc_link_edges = resolve_doc_links(doc_links_dict, rel_paths)
    for e in doc_link_edges:
        source_id = file_id_of.get(e["source"])
        target_id = file_id_of.get(e["target"])
        if source_id and target_id:
            graph["edges"].append(
                {
                    "source": f"file:{source_id}",
                    "target": f"file:{target_id}",
                    "relation": "references",
                    "confidence": e["confidence"],
                }
            )

    # Step 7: resolve table references and convert to graph edges (FILE to SYMBOL)
    # Build table_symbols map: lower(table_name) -> [(rel_path, symbol_id), ...]
    table_symbols_map = {}
    for sym in symbols:
        if sym.get("kind") == "sql_table":
            table_name = sym.get("name", "").lower()
            # Find the rel_path for this symbol
            for f in files:
                if f["file_id"] == sym["file_id"]:
                    if table_name not in table_symbols_map:
                        table_symbols_map[table_name] = []
                    table_symbols_map[table_name].append((f["rel_path"], sym["symbol_id"]))
                    break

    table_ref_edges = resolve_table_refs(table_refs_dict, table_symbols_map)
    for e in table_ref_edges:
        source_id = file_id_of.get(e["source"])
        target_symbol_id = e.get("target_symbol_id")
        if source_id and target_symbol_id:
            graph["edges"].append(
                {
                    "source": f"file:{source_id}",
                    "target": f"symbol:{target_symbol_id}",
                    "relation": "references",
                    "confidence": e["confidence"],
                }
            )

    # Step 8: drop any edge with missing endpoints
    node_ids = {n["id"] for n in graph["nodes"]}
    graph["edges"] = [e for e in graph["edges"] if e.get("source") in node_ids and e.get("target") in node_ids]

    # Step 9: assign communities
    graph["nodes"] = assign_communities(graph["nodes"], graph["edges"])

    # Step 10: re-sort for determinism
    graph["nodes"] = sorted(graph["nodes"], key=lambda n: n["id"])
    graph["edges"] = sorted(graph["edges"], key=lambda e: (e["source"], e["target"], e["relation"]))

    return graph


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
        "edges": len(edges),
        "index": str(final),
        "generation": generation_id,
        "graph_engine": graph["engine"],
        "semantic_chunks": semantic["chunks"],
        "semantic_backend": semantic["backend"],
    }
