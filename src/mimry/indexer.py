from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

from .config_manifest_adapter import is_config_manifest
from .constants import SCHEMA_VERSION
from .core.build import GraphEngine, canonicalize_edges, validate_edge_identity
from .core.cluster import assign_communities
from .core.report import build_manifest, render_report
from .core.resolve import (
    resolve_calls,
    resolve_doc_links,
    resolve_imports,
    resolve_inheritance,
    resolve_table_refs,
)
from .paths import graph_output_dir, idx_path, now, pointer_file
from .reuse import (
    ADAPT_CACHE,
    INDEXED,
    SENSITIVE,
    STAT_CACHE,
    adapt_cache_header,
    cache_checksums,
    code_fingerprint,
    identity,
    load_adapt_cache,
    load_stat_cache,
)
from .scanner import (
    FileChangedError,
    SensitiveContentError,
    UnopenableFileError,
    adapt,
    body_terms,
    read_snapshot,
    scan_stats,
)
from .security import is_text, sanitize_data
from .semantic import build_semantic_index, file_semantic_chunks
from .state import (
    GENERATION_MANIFEST,
    UNINDEXABLE_FILE,
    StateCorruptionError,
    _fsync_directory,
    atomic_write_json,
    atomic_write_text,
    backup_path,
    fsync_tree,
    generation_manifest,
    load_json_state,
    replace_path,
    validate_generation,
)
from .storage import (
    FILES_FTS_CREATE,
    active_index_pointer,
    connect,
    register_root,
    save_pointer,
    write_jsonl,
)


def _fault(point: str) -> None:
    """Deterministic subprocess-only crash hook for recovery tests."""
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
        # Index data is rebuildable. A damaged legacy DB must not be
        # copied into a new generation; sidecar corruption is still
        # surfaced on normal reads.
        try:
            (staging / "mimry.sqlite").unlink()
        except FileNotFoundError:
            pass


class _Progress:
    """One self-overwriting status line on stderr.

    Only for an interactive terminal.
    """

    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.last = 0.0

    def __call__(self, message: str, *, force: bool = False) -> None:
        if not self.enabled:
            return
        clock = time.monotonic()
        if force or clock - self.last >= 0.1:
            self.last = clock
            sys.stderr.write(f"\r\x1b[K{message}")
            sys.stderr.flush()

    def done(self) -> None:
        if self.enabled:
            sys.stderr.write("\r\x1b[K")
            sys.stderr.flush()


def _stderr_is_tty() -> bool:
    try:
        return sys.stderr.isatty()
    except (AttributeError, ValueError):
        return False


def _jsonl_line(row) -> str:
    return json.dumps(sanitize_data(row), sort_keys=True)


def _symbol_order(symbol: dict) -> tuple:
    return (
        symbol["file_id"],
        symbol["name"],
        symbol["kind"],
        symbol.get("line_start") or 0,
        symbol["symbol_id"],
    )


SKIPPED = "skipped"
UNINDEXABLE = "unindexable"
# Below this many files, worker start-up (a fresh interpreter importing
# the parsers) costs more than it saves.
PARALLEL_MIN_FILES = 64
_REFERENCE_KEYS = ("doc_links", "table_refs", "inherits", "alembic", "xcode")


def _adapt_file(path: Path, root: Path, rel_path: str) -> tuple[str, list | None, str | None]:
    """Adapt one file into ``(outcome, stat identity, cache line)``.

    Runs in worker processes, so it returns only picklable,
    already-sanitized data; the line is exactly what an incremental run
    later replays.
    """
    try:
        snapshot = read_snapshot(path, root=root)
        output = adapt(path, root, snapshot)
    except UnopenableFileError:
        # Locked or ACL-protected: skipped like ignored files, not
        # refused.
        return SKIPPED, None, None
    except SensitiveContentError:
        return SENSITIVE, identity(snapshot[1]), None
    except (FileChangedError, OSError, UnicodeError, ValueError):
        # adapt() also raises ValueError when its output carries
        # sensitive data.
        return UNINDEXABLE, None, None
    row = sanitize_data({"rel_path": rel_path, "out": output})
    # Semantic chunks derive from exactly the persisted (sanitized)
    # record and its symbols in symbols.jsonl order, so they are
    # computed here, in parallel, and replayed on reuse instead of being
    # rebuilt serially for every file.
    file_rec, file_symbols = row["out"][0], row["out"][1]
    row["chunks"] = file_semantic_chunks(file_rec, sorted(file_symbols, key=_symbol_order))
    # Whole-file identifier terms; content_hint covers only first 40.
    row["fts_body"] = body_terms(snapshot[0]) if is_text(path) else ""
    return INDEXED, identity(snapshot[1]), json.dumps(row, sort_keys=True)


def _index_workers(file_count: int) -> int:
    configured = os.environ.get("MIMRY_INDEX_WORKERS")
    if configured and configured.isdigit():
        return max(1, min(int(configured), 61))
    if file_count < PARALLEL_MIN_FILES:
        return 1
    # ponytail: capped because each worker holds its own parsers; raise
    # it if indexing stays CPU-bound on bigger machines.
    return max(1, min(os.cpu_count() or 1, 16))


def _adapt_all(root: Path, todo: list[tuple[str, Path]], progress, total: int) -> list:
    """Adapt ``todo`` in order, across worker processes when large."""
    done_offset = total - len(todo)
    workers = _index_workers(len(todo))
    if workers > 1:
        try:
            results = []
            with ProcessPoolExecutor(max_workers=workers) as pool:
                chunksize = max(1, len(todo) // (workers * 8))
                mapped = pool.map(
                    _adapt_file,
                    [p for _, p in todo],
                    [root] * len(todo),
                    [r for r, _ in todo],
                    chunksize=chunksize,
                )
                for number, result in enumerate(mapped, 1):
                    progress(f"MIMRY: indexing {done_offset + number}/{total} files")
                    results.append(result)
            return results
        except (OSError, BrokenProcessPool):
            # No usable worker processes (sandboxed or resource-limited
            # host): adaptation is pure, so redoing it in-process gives
            # the same result.
            pass
    results = []
    for number, (rel_path, path) in enumerate(todo, 1):
        progress(f"MIMRY: indexing {done_offset + number}/{total} files")
        results.append(_adapt_file(path, root, rel_path))
    return results


def _collect(
    root: Path, *, previous: Path | None = None, record: dict | None = None, progress=None
):
    """Adapt every candidate file; reuse ``previous`` output for
    stat-unchanged ones.

    ``record``, when given, receives the new generation's stat
    identities and per-file adapter output for the next incremental run.
    """
    progress = progress or _Progress(False)
    stat_cache = load_stat_cache(previous) if previous is not None else None
    adapt_cache = load_adapt_cache(previous, root) if stat_cache is not None else None
    stat_entries: dict[str, list] = {}
    # Serialized as each file is adapted, before anything downstream can
    # touch the records, so a later reuse replays exactly what adapt()
    # returned.
    adapt_lines: list[str] = []
    files = []
    symbols = []
    edges = []
    imports = {}
    exports = {}
    calls = {}
    references = {}
    symbols_by_file = {}
    # Paths deliberately kept out of the index: secret-bearing or
    # unreadable. They are recorded so freshness can tell "MIMRY refused
    # this" from "the user changed this". Without it a single
    # secret-bearing file reports as changed on every run, the index
    # never reaches `current`, and that pins graph health to stale --
    # which disables `mimry path` entirely. Content never leaves this
    # list; only the path is kept.
    unindexable: list[str] = []

    def _note_unindexable(path: Path) -> None:
        try:
            unindexable.append(path.relative_to(root).as_posix())
        except ValueError:
            pass

    progress("MIMRY: scanning files", force=True)
    # No open probe here: the snapshot read opens each file once, and a
    # file that cannot be opened is skipped at that point instead.
    candidates = scan_stats(root, probe_open=False)
    reused: dict[str, tuple[str, dict] | None] = {}
    chunks_by_file_id: dict[str, list] = {}
    fts_body_by_file_id: dict[str, str] = {}
    todo: list[tuple[str, Path]] = []
    for rel_path, path, live in candidates:
        # Config manifests read sibling lockfiles, so their output is
        # not a function of their own bytes alone; always re-adapt them.
        if adapt_cache is not None and not is_config_manifest(path):
            if stat_cache.trusts(rel_path, live, INDEXED) and rel_path in adapt_cache:
                reused[rel_path] = adapt_cache[rel_path]
                stat_entries[rel_path] = stat_cache.entries[rel_path]
                continue
            if stat_cache.trusts(rel_path, live, SENSITIVE):
                reused[rel_path] = None
                stat_entries[rel_path] = stat_cache.entries[rel_path]
                continue
        todo.append((rel_path, path))
    adapted = dict(
        zip(
            (rel for rel, _ in todo),
            _adapt_all(root, todo, progress, len(candidates)),
            strict=True,
        )
    )

    for rel_path, path, _live in candidates:
        if rel_path in reused:
            cached = reused[rel_path]
            if cached is None:
                continue
            line, row = cached
        else:
            outcome, stat_identity, line = adapted[rel_path]
            if outcome == SKIPPED:
                continue
            if outcome == SENSITIVE:
                # Secret-bearing source stays unrecorded, so freshness
                # re-checks it and reports it once its bytes become
                # indexable again.
                stat_entries[rel_path] = [*stat_identity, SENSITIVE]
                continue
            if outcome == UNINDEXABLE:
                _note_unindexable(path)
                continue
            stat_entries[rel_path] = [*stat_identity, INDEXED]
            row = json.loads(line)
        adapt_lines.append(line)
        output = row["out"]
        if row.get("chunks") is not None:
            chunks_by_file_id[output[0]["file_id"]] = row["chunks"]
        if row.get("fts_body") is not None:
            fts_body_by_file_id[output[0]["file_id"]] = row["fts_body"]
        (
            file_rec,
            file_symbols,
            file_edges,
            file_imports,
            file_exports,
            file_calls,
            file_references,
        ) = output
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
        # Accumulate references only if there's at least one non-empty
        # list
        if any(file_references.get(key) for key in _REFERENCE_KEYS):
            references[file_rec["rel_path"]] = file_references

    # Sort all collections for determinism
    files = sorted(files, key=lambda f: f["rel_path"])
    symbols = sorted(symbols, key=_symbol_order)
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
        (k, sorted(v, key=lambda c: (c.get("name", ""), c.get("line") or 0)))
        for k, v in sorted(calls.items())
    ]
    calls = dict(calls_items)
    symbols_by_file = dict(sorted(symbols_by_file.items()))
    references = dict(sorted(references.items()))

    # The persistence boundary, not just graph construction. Enforcing
    # this only inside build_graph would leave every other consumer of
    # _collect -- and any future writer -- free to persist an ambiguous
    # edge ID.
    edges = validate_edge_identity(edges)

    if record is not None:
        record["stat_entries"] = stat_entries
        record["adapt_lines"] = adapt_lines
        record["chunks"] = chunks_by_file_id
        record["fts_body"] = fts_body_by_file_id
        # What the previous generation was built from, to recognise a
        # no-op run.
        record["previous_lines"] = (
            [line for line, _row in adapt_cache.values()] if adapt_cache is not None else None
        )
        record["previous_kinds"] = (
            {rel: entry[4] for rel, entry in stat_cache.entries.items()}
            if stat_cache is not None
            else None
        )
    return (
        files,
        symbols,
        edges,
        imports,
        exports,
        calls,
        symbols_by_file,
        references,
        sorted(set(unindexable)),
    )


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


def _split_references(references) -> dict[str, dict]:
    """Split the per-file references bag into one dict per relation."""
    result = {key: {} for key in _REFERENCE_KEYS}
    for rel_path, data in (references or {}).items():
        for key in _REFERENCE_KEYS:
            if data.get(key):
                result[key][rel_path] = data[key]
    return result


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
        caller = e.get("caller_symbol_id") or symbol_ids.get(
            (e["caller_file"], e["caller_symbol"])
        )
        target = e.get("target_symbol_id") or symbol_ids.get(
            (e["target_file"], e["target_symbol"])
        )
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


def _revises_graph_edges(alembic_data, file_id_of) -> list[dict]:
    """file -> file, from Alembic migration lineage."""
    edges = []

    rev_to_files: dict[str, list[str]] = {}

    for rel_path, mig_info in alembic_data.items():
        source_id = file_id_of.get(rel_path)
        if not source_id or not mig_info:
            continue
        rev = mig_info.get("revision")
        if rev:
            if rev not in rev_to_files:
                rev_to_files[rev] = []
            rev_to_files[rev].append(source_id)

    for rel_path, mig_info in alembic_data.items():
        source_id = file_id_of.get(rel_path)
        if not source_id or not mig_info:
            continue
        down_revs = mig_info.get("down_revisions", [])
        for down_rev in down_revs:
            targets = rev_to_files.get(down_rev, [])
            if len(targets) != 1:
                continue
            target_id = targets[0]
            if target_id != source_id:
                edges.append(
                    _edge(f"file:{source_id}", f"file:{target_id}", "revises", "EXTRACTED")
                )
    return edges


def _doc_link_graph_edges(doc_links, rel_paths, file_id_of) -> list[dict]:
    """file -> file, from markdown links."""
    edges = []
    for e in resolve_doc_links(doc_links, rel_paths):
        source, target = file_id_of.get(e["source"]), file_id_of.get(e["target"])
        if source and target:
            edges.append(_edge(f"file:{source}", f"file:{target}", "references", e["confidence"]))
    return edges


def _xcode_reference_edges(xcode_refs, rel_paths, file_id_of) -> list[dict]:
    """file -> file, from Xcode project references."""
    edges = []
    for source_rel, target_rels in xcode_refs.items():
        source_id = file_id_of.get(source_rel)
        if not source_id:
            continue
        for target_rel in target_rels:
            if target_rel in rel_paths:
                target_id = file_id_of.get(target_rel)
                if target_id:
                    edges.append(
                        _edge(f"file:{source_id}", f"file:{target_id}", "references", "EXTRACTED")
                    )
    return edges


def _table_ref_graph_edges(table_refs, table_symbols, file_id_of) -> list[dict]:
    """file -> symbol, from SQL table mentions."""
    edges = []
    for e in resolve_table_refs(table_refs, table_symbols):
        source, target = file_id_of.get(e["source"]), e.get("target_symbol_id")
        if source and target:
            edges.append(
                _edge(f"file:{source}", f"symbol:{target}", "references", e["confidence"])
            )
    return edges


def _finalize_graph(graph) -> dict:
    """Drop dangling edges, cluster, and sort.

    The sort is not cosmetic: graph.json is checksummed by the
    generation manifest, so an unstable order would break generation
    coherence.
    """
    node_ids = {n["id"] for n in graph["nodes"]}
    graph["edges"] = [
        e for e in graph["edges"] if e.get("source") in node_ids and e.get("target") in node_ids
    ]
    # (source, target, relation) is not a total key: equal-endpoint
    # edges that differ only in confidence kept insertion order.
    # canonicalize_edges applies the documented EXTRACTED-over-INFERRED
    # policy and a total sort key.
    graph["edges"] = canonicalize_edges(graph["edges"])
    graph["nodes"] = sorted(
        assign_communities(graph["nodes"], graph["edges"]), key=lambda n: n["id"]
    )
    return graph


def _build_core_graph(files, symbols, edges, imports, exports, calls, symbols_by_file, references):
    """Assemble the relationship graph.

    defines + imports + calls + inherits + references.
    """
    graph = GraphEngine().build_graph(files, symbols, edges, imports=imports, exports=exports)

    rel_paths = {f["rel_path"] for f in files}
    file_id_of = {f["rel_path"]: f["file_id"] for f in files}
    symbol_ids = _symbol_ids_by_path_name(files, symbols)
    import_edges = resolve_imports(imports, rel_paths)
    split_refs = _split_references(references)

    graph["edges"] += _import_graph_edges(import_edges, file_id_of)
    graph["edges"] += _call_graph_edges(calls, symbols_by_file, import_edges, symbol_ids)
    graph["edges"] += _inheritance_graph_edges(
        split_refs["inherits"], symbols_by_file, import_edges, symbol_ids
    )
    graph["edges"] += _doc_link_graph_edges(split_refs["doc_links"], rel_paths, file_id_of)
    graph["edges"] += _table_ref_graph_edges(
        split_refs["table_refs"], _table_symbols(files, symbols), file_id_of
    )
    graph["edges"] += _revises_graph_edges(split_refs["alembic"], file_id_of)
    graph["edges"] += _xcode_reference_edges(split_refs["xcode"], rel_paths, file_id_of)

    return _finalize_graph(graph)


def _remove_tree(path: Path) -> None:
    """Remove a cache tree completely or fail.

    Never reports a false success.
    """
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
    """Retain only pointer-current + readable LKG and remove crash
    leftovers.

    The caller must hold ``base/operation.lock`` exclusively so staging
    and final generation cleanup cannot race publication or a
    current-root cache wipe.
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


def _unchanged_generation(root, ptr, previous, record, files, symbols, unindexable) -> dict | None:
    """The active generation's stats when this run would republish it
    unchanged.

    Every published artifact is a function of the per-file adapter
    lines, the refused paths and the sensitive-file set, so when all
    three match the previous run the graph, semantic index and sidecars
    would come out byte for byte the same. Keep the generation instead
    of rebuilding it. Any doubt -- missing graph artifacts, a generation
    that fails validation, a manifest from before this check -- means
    publishing normally.
    """
    if previous is None or record["previous_lines"] != record["adapt_lines"]:
        return None
    if record["previous_kinds"] != {
        rel: entry[4] for rel, entry in record["stat_entries"].items()
    }:
        return None
    out = graph_output_dir(root)
    if not all(
        (out / name).is_file() for name in ("graph.json", "GRAPH_REPORT.md", "manifest.json")
    ):
        return None
    try:
        validate_generation(ptr)
        manifest, _ = load_json_state(previous / GENERATION_MANIFEST)
        previous_unindexable, _ = load_json_state(previous / UNINDEXABLE_FILE)
    except (StateCorruptionError, OSError, ValueError):
        return None
    stats = manifest.get("stats") if isinstance(manifest, dict) else None
    if not isinstance(stats, dict) or previous_unindexable != unindexable:
        return None
    return {
        "files": len(files),
        "symbols": len(symbols),
        "index": str(previous),
        "generation": ptr["generationId"],
        **stats,
        "unchanged": True,
    }


def _changes(ptr: dict, files: list[dict]) -> dict | None:
    """Paths added, changed and removed since the active generation.

    None on a first index.
    """
    if not ptr.get("generationId"):
        return None
    try:
        before, _ = load_json_state(Path(ptr["indexPath"]) / "file-hashes.json", default=None)
    except (StateCorruptionError, OSError, ValueError):
        return None
    if not isinstance(before, dict):
        return None
    old = {
        path: entry.get("hash") if isinstance(entry, dict) else None
        for path, entry in before.items()
    }
    new = {file_rec["rel_path"]: file_rec["hash"] for file_rec in files}
    return {
        "added": sorted(new.keys() - old.keys()),
        "changed": sorted(path for path in new.keys() & old.keys() if new[path] != old[path]),
        "removed": sorted(old.keys() - new.keys()),
    }


def write_index(root, ptr, *, full: bool = False):
    """Build and publish a new generation.

    Files whose lstat still matches the previous generation's recorded
    snapshot reuse its adapter output instead of being read and parsed
    again; ``full`` re-adapts every file. Either way the published
    artifacts are identical when the tree is.
    """
    root = Path(root)
    progress = _Progress(_stderr_is_tty())
    base = idx_path(ptr["rootId"])
    generations = base / "generations"
    base.mkdir(parents=True, exist_ok=True)

    # The exclusive operation lock serializes publication/legacy
    # migration and prevents GC from deleting generations retained by
    # shared readers.
    with active_index_pointer(
        root,
        exclusive=True,
        validate=False,
        normalize_stale_index_path=True,
    ) as active:
        if not active or active.get("rootId") != ptr.get("rootId"):
            raise RuntimeError(
                "MIMRY root pointer changed while waiting for the operation lock; retry indexing"
            )
        ptr = active

        # A pointer written under an older identity schema names a
        # generation whose file, symbol, and chunk IDs were derived from
        # the absolute checkout path. Drop it rather than seeding the
        # new generation's SQLite from it, which would carry stale rows
        # keyed by IDs nothing else in this build produces.
        if ptr.get("schemaVersion") not in (None, SCHEMA_VERSION):
            ptr = {**ptr, "indexPath": str(base), "lastIndexedAt": None}
            ptr.pop("generationId", None)

        _cleanup_generations(root, base, ptr)
        previous = None if full or not ptr.get("generationId") else Path(ptr["indexPath"])
        record: dict = {}
        # Taken before the first file is read: any file modified after
        # this instant is racy and never trusted by its stat identity
        # alone.
        scan_started_ns = time.time_ns()
        try:
            (
                files,
                symbols,
                edges,
                imports,
                exports,
                calls,
                symbols_by_file,
                references,
                unindexable,
            ) = _collect(root, previous=previous, record=record, progress=progress)
            changes = _changes(ptr, files)
            # Unrecorded as well as unindexable: report every file kept
            # out.
            skipped = len(unindexable) + sum(
                entry[4] == SENSITIVE for entry in record["stat_entries"].values()
            )
            kept = _unchanged_generation(root, ptr, previous, record, files, symbols, unindexable)
            if kept is not None:
                return {**kept, "changes": changes, "unindexable": skipped}
            progress("MIMRY: building graph", force=True)
            graph = _build_core_graph(
                files, symbols, edges, imports, exports, calls, symbols_by_file, references
            )
        finally:
            progress.done()
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
                con.execute("drop table if exists files_fts")
                con.execute(FILES_FTS_CREATE)
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
                    fts_body = record.get("fts_body", {}).get(file_rec["file_id"], "")
                    con.execute(
                        "insert into files_fts values(?,?,?,?,?,?,?)",
                        (
                            file_rec["file_id"],
                            file_rec["rel_path"],
                            file_rec["filename"],
                            file_rec["extension"],
                            file_rec["content_hint"],
                            file_rec["metadata_text"],
                            fts_body,
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
            write_jsonl(
                staging / "imports.jsonl",
                [{"file": key, "imports": value} for key, value in imports.items()],
            )
            write_jsonl(
                staging / "exports.jsonl",
                [{"file": key, "exports": value} for key, value in exports.items()],
            )
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
            stat_cache = {
                "scanStartedNs": scan_started_ns,
                "entries": record["stat_entries"],
                "fingerprint": code_fingerprint(),
            }
            atomic_write_text(
                staging / STAT_CACHE, json.dumps(stat_cache, separators=(",", ":"), sort_keys=True)
            )
            header = json.dumps(adapt_cache_header(root), sort_keys=True)
            atomic_write_text(
                staging / ADAPT_CACHE,
                "".join(f"{line}\n" for line in [header, *record["adapt_lines"]]),
            )
            progress("MIMRY: building semantic index", force=True)
            semantic = build_semantic_index(
                staging, ptr["rootId"], generation_id, precomputed=record["chunks"]
            )
            _fault("after-sidecars")

            progress("MIMRY: publishing index", force=True)
            manifest = generation_manifest(staging, generation_id, indexed_at)
            manifest["caches"] = cache_checksums(staging)
            manifest["stats"] = {
                "edges": len(graph["edges"]),
                "graph_engine": graph["engine"],
                "semantic_chunks": semantic["chunks"],
                "semantic_backend": semantic["backend"],
            }
            atomic_write_json(staging / GENERATION_MANIFEST, manifest)
            fsync_tree(staging)
            replace_path(staging, final)
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
            atomic_write_text(
                out / "GRAPH_REPORT.md", render_report(graph, commit=_git_commit(root))
            )
            atomic_write_json(out / "manifest.json", build_manifest(files))
        except BaseException:
            if staging.exists() or staging.is_symlink():
                _remove_tree(staging)
            raise
        finally:
            progress.done()

    return {
        "files": len(files),
        "symbols": len(symbols),
        "edges": len(graph["edges"]),
        "index": str(final),
        "generation": generation_id,
        "graph_engine": graph["engine"],
        "semantic_chunks": semantic["chunks"],
        "semantic_backend": semantic["backend"],
        "changes": changes,
        "unindexable": skipped,
    }
