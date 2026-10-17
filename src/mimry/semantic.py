from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from .constants import SCHEMA_VERSION
from .intent import query_terms
from .paths import now, stable_id
from .security import contains_sensitive_text, path_has_ignored_part, redact_sensitive_text
from .state import semantic_rows_checksum

SEMANTIC_BACKEND = "local-hash-v1"
SEMANTIC_SCHEMA_VERSION = "0.1.0"
VECTOR_DIMS = 256
MAX_PREVIEW = 500

# Measurement switches. Both are off when unset, so the default backend
# and ranking stay exactly as shipped.
MODEL_ENV = "MIMRY_SEMANTIC_MODEL"
FUSION_ENV = "MIMRY_SEMANTIC_FUSION"
WEIGHT_ENV = "MIMRY_SEMANTIC_WEIGHT"
MODEL_FLOOR = 0.2
MODEL_CHUNKS_PER_FILE = 3
RRF_K = 60
_EMBED_BATCH = 256
_STATIC_MODELS: dict[str, Any] = {}

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def ensure_semantic_schema(con: sqlite3.Connection) -> None:
    con.execute(
        """
        create table if not exists semantic_chunks(
            chunk_id text primary key,
            root_id text not null,
            file_id text,
            rel_path text not null,
            chunk_kind text not null,
            chunk_text_hash text not null,
            chunk_text_preview text,
            vector_json text not null,
            model_name text not null,
            backend_name text not null,
            created_at text not null,
            schema_version text not null,
            generation_id text
        )
        """
    )
    chunk_columns = {row[1] for row in con.execute("pragma table_info(semantic_chunks)")}
    if "generation_id" not in chunk_columns:
        con.execute("alter table semantic_chunks add column generation_id text")
    con.execute(
        "create index if not exists semantic_chunks_root_path_idx on semantic_chunks(root_id,"
        " rel_path)"
    )
    con.execute(
        """
        create table if not exists semantic_metadata(
            root_id text primary key,
            backend_name text not null,
            indexed_at text not null,
            chunk_count integer not null,
            schema_version text not null,
            generation_id text,
            content_checksum text
        )
        """
    )
    metadata_columns = {row[1] for row in con.execute("pragma table_info(semantic_metadata)")}
    if "generation_id" not in metadata_columns:
        con.execute("alter table semantic_metadata add column generation_id text")
    if "content_checksum" not in metadata_columns:
        con.execute("alter table semantic_metadata add column content_checksum text")


def _tokens(text: str) -> list[str]:
    raw = [token.lower() for token in _TOKEN_RE.findall(text)]
    expanded: list[str] = []
    for token in raw:
        if not token:
            continue
        expanded.append(token)
        for part in re.findall(r"[a-z]+|[0-9]+", token):
            if part != token:
                expanded.append(part)
    return expanded


def _hash_index(token: str) -> int:
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % VECTOR_DIMS


def vectorize(text: str) -> dict[str, float]:
    counts: dict[int, float] = {}
    for token in _tokens(text):
        index = _hash_index(token)
        counts[index] = counts.get(index, 0.0) + 1.0
    if not counts:
        return {}
    norm = math.sqrt(sum(value * value for value in counts.values())) or 1.0
    return {str(k): round(v / norm, 6) for k, v in sorted(counts.items())}


def cosine_sparse(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(key, 0.0) for key, value in left.items())


def configured_model() -> str | None:
    """Model name from MIMRY_SEMANTIC_MODEL, or None when unset."""
    return os.environ.get(MODEL_ENV, "").strip() or None


def active_backend() -> str:
    name = configured_model()
    return f"model2vec:{name}" if name else SEMANTIC_BACKEND


def fusion_mode() -> str:
    raw = os.environ.get(FUSION_ENV, "").strip().lower()
    if raw in ("", "bonus"):
        return "bonus"
    if raw == "rrf":
        return "rrf"
    raise ValueError(f"{FUSION_ENV} must be 'bonus' or 'rrf', not {raw!r}")


def _load_static_model(name: str) -> Any:
    cached = _STATIC_MODELS.get(name)
    if cached is None:
        try:
            from model2vec import StaticModel
        except ImportError as exc:
            raise RuntimeError(
                f"{MODEL_ENV}={name} needs the model2vec package, which is not"
                " installed. Install it, or unset the variable."
            ) from exc
        cached = StaticModel.from_pretrained(name)
        _STATIC_MODELS[name] = cached
    return cached


def _unit(row: Any) -> list[float]:
    values = [float(value) for value in row]
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [round(value / norm, 5) for value in values]


def _embed(texts: list[str], name: str) -> list[list[float]]:
    model = _load_static_model(name)
    vectors: list[list[float]] = []
    for start in range(0, len(texts), _EMBED_BATCH):
        for row in model.encode(texts[start : start + _EMBED_BATCH]):
            vectors.append(_unit(row))
    return vectors


def _dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=False))


def _bounded_preview(text: str) -> str:
    return " ".join(text.split())[:MAX_PREVIEW]


def _chunk_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def _chunk(
    root_id: str, file_id: str, rel_path: str, kind: str, text: str, created_at: str
) -> dict[str, Any] | None:
    if contains_sensitive_text(text) or contains_sensitive_text(rel_path):
        return None
    text = redact_sensitive_text(text)
    preview = _bounded_preview(text)
    backend = active_backend()
    if backend == SEMANTIC_BACKEND:
        vector = vectorize(text)
        if not preview or not vector:
            return None
    else:
        # The model embeds every chunk in one batch in
        # build_semantic_index, which then drops the full text.
        if not preview:
            return None
        vector = None
    chunk = {
        # Canonical semantic identity, so it must not carry root_id:
        # that is a uuid4 minted by `mimry init`, and two identical
        # checkouts would then disagree on every chunk id. Same rule as
        # file_id and the checkout path.
        "chunk_id": stable_id(SCHEMA_VERSION, rel_path, kind, _chunk_text_hash(text)),
        "root_id": root_id,
        "file_id": file_id,
        "rel_path": rel_path,
        "chunk_kind": kind,
        "chunk_text_hash": _chunk_text_hash(text),
        "chunk_text_preview": preview,
        "vector": vector,
        "model_name": backend,
        "backend_name": backend,
        "created_at": created_at,
        "schema_version": SEMANTIC_SCHEMA_VERSION,
    }
    if vector is None:
        chunk["text"] = text
    return chunk


def _embed_chunks(chunks: list[dict[str, Any]], name: str) -> None:
    """Embed model chunks in one batch and drop their full text."""
    vectors = _embed([chunk.pop("text") for chunk in chunks], name)
    for chunk, vector in zip(chunks, vectors, strict=True):
        chunk["vector"] = vector


def _semantic_chunks_for_file(
    root_id: str, file_rec: dict[str, Any], symbols: list[dict[str, Any]], created_at: str
) -> list[dict[str, Any]]:
    rel_path = file_rec["rel_path"]
    file_id = file_rec["file_id"]
    chunks: list[dict[str, Any]] = []
    candidates = [
        ("path", f"{rel_path} {file_rec.get('filename', '')} {file_rec.get('extension', '')}"),
        ("content_hint", file_rec.get("content_hint", "")),
    ]
    metadata = file_rec.get("metadata_text", "") or ""
    if metadata and metadata != file_rec.get("content_hint", ""):
        candidates.append(("adapter_fact", metadata))
    symbol_names = " ".join(
        sym.get("name", "") for sym in symbols if sym.get("file_id") == file_id
    )
    if symbol_names:
        candidates.append(("symbol", f"{rel_path} {symbol_names}"))
    if file_rec.get("extension") in {".md", ".mdx", ".rst"}:
        headings = " ".join(
            part for part in metadata.split(" | ") if "markdown headings" in part.lower()
        )
        if headings:
            candidates.append(("doc_heading", headings))

    seen: set[tuple[str, str]] = set()
    for kind, text in candidates:
        if not text:
            continue
        key = (kind, _chunk_text_hash(text))
        if key in seen:
            continue
        seen.add(key)
        chunk = _chunk(root_id, file_id, rel_path, kind, text, created_at)
        if chunk:
            chunks.append(chunk)
    return chunks


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def file_semantic_chunks(
    file_rec: dict[str, Any], symbols: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """One file's chunks, independent of the root and build time.

    ``symbols`` must be the file's symbols in symbols.jsonl order. The
    root_id and created_at placeholders are filled in by
    build_semantic_index.
    """
    return _semantic_chunks_for_file("", file_rec, symbols, "")


def build_semantic_index(
    idx: Path,
    root_id: str,
    generation_id: str | None = None,
    precomputed: dict[str, list] | None = None,
) -> dict[str, Any]:
    """Rebuild the generation's semantic chunks.

    ``precomputed`` maps file_id to file_semantic_chunks() output for
    files whose chunks were already derived (in index workers, or
    reused); the rest are computed here. Either way the rows are
    identical.
    """
    precomputed = precomputed or {}
    files = _load_jsonl(idx / "files.jsonl")
    symbols = _load_jsonl(idx / "symbols.jsonl")
    # Group once: filtering the full symbol list per file was quadratic,
    # and dominated indexing on large repositories. Order within a file
    # is preserved.
    symbols_by_file: dict[Any, list[dict[str, Any]]] = {}
    for sym in symbols:
        symbols_by_file.setdefault(sym.get("file_id"), []).append(sym)
    created_at = now()
    chunks: list[dict[str, Any]] = []
    for file_rec in files:
        known = precomputed.get(file_rec["file_id"])
        if known is not None:
            chunks.extend(
                {**chunk, "root_id": root_id, "created_at": created_at} for chunk in known
            )
            continue
        file_symbols = symbols_by_file.get(file_rec["file_id"], [])
        chunks.extend(_semantic_chunks_for_file(root_id, file_rec, file_symbols, created_at))
    backend = active_backend()
    model_name = configured_model()
    if model_name:
        _embed_chunks(chunks, model_name)

    con = sqlite3.connect(idx / "mimry.sqlite")
    try:
        ensure_semantic_schema(con)
        generation_columns = {row[1] for row in con.execute("pragma table_info(index_generation)")}
        if "semantic_checksum" not in generation_columns:
            con.execute("alter table index_generation add column semantic_checksum text")
        if generation_id is None:
            generation_row = con.execute(
                "select generation_id from index_generation limit 1"
            ).fetchone()
            generation_id = generation_row[0] if generation_row else None
        checksum_rows = [
            (
                chunk["chunk_id"],
                chunk["root_id"],
                chunk["file_id"],
                chunk["rel_path"],
                chunk["chunk_kind"],
                chunk["chunk_text_hash"],
                chunk["chunk_text_preview"],
                json.dumps(chunk["vector"], sort_keys=True),
                chunk["model_name"],
                chunk["backend_name"],
                chunk["created_at"],
                chunk["schema_version"],
                generation_id,
            )
            for chunk in chunks
        ]
        content_checksum = semantic_rows_checksum(checksum_rows)
        with con:
            con.execute("delete from semantic_chunks where root_id = ?", (root_id,))
            for _chunk, checksum_row in zip(chunks, checksum_rows, strict=True):
                con.execute(
                    """
                    insert or replace into semantic_chunks(
                        chunk_id, root_id, file_id, rel_path, chunk_kind, chunk_text_hash,
                        chunk_text_preview, vector_json, model_name, backend_name, created_at,
                        schema_version, generation_id
                    ) values(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    checksum_row,
                )
            con.execute(
                """insert or replace into semantic_metadata(
                       root_id, backend_name, indexed_at, chunk_count, schema_version,
                       generation_id, content_checksum
                   ) values(?,?,?,?,?,?,?)""",
                (
                    root_id,
                    backend,
                    created_at,
                    len(chunks),
                    SEMANTIC_SCHEMA_VERSION,
                    generation_id,
                    content_checksum,
                ),
            )
            if generation_id is not None:
                con.execute(
                    "update index_generation set semantic_checksum = ? where generation_id = ?",
                    (content_checksum, generation_id),
                )
    finally:
        con.close()
    return {
        "backend": backend,
        "chunks": len(chunks),
        "indexed_at": created_at,
        "generation": generation_id,
        "checksum": content_checksum,
    }


def semantic_health(
    idx: Path, root_id: str | None, expected_files: int | None = None
) -> dict[str, Any]:
    backend = active_backend()
    if not root_id or not (idx / "mimry.sqlite").exists():
        return {"status": "missing", "backend": backend, "chunks": 0, "indexed_at": None}
    con = sqlite3.connect(idx / "mimry.sqlite")
    con.row_factory = sqlite3.Row
    try:
        ensure_semantic_schema(con)
        row = con.execute(
            "select * from semantic_metadata where root_id = ?", (root_id,)
        ).fetchone()
        if not row:
            return {
                "status": "missing",
                "backend": backend,
                "chunks": 0,
                "indexed_at": None,
            }
        chunks = int(row["chunk_count"] or 0)
        policy_excluded_chunks = sum(
            1
            for chunk in con.execute(
                "select rel_path from semantic_chunks where root_id = ?", (root_id,)
            )
            if path_has_ignored_part(chunk["rel_path"])
        )
        status = "current" if chunks > 0 else "missing"
        # Another backend ranks in a different vector space, so an index
        # built with it is not current.
        if row["backend_name"] != backend:
            status = "stale"
        if policy_excluded_chunks or (
            expected_files is not None and expected_files > 0 and chunks < expected_files
        ):
            status = "stale"
        return {
            "status": status,
            "backend": row["backend_name"],
            "chunks": chunks - policy_excluded_chunks,
            "indexed_at": row["indexed_at"],
            "policy_excluded_chunks": policy_excluded_chunks,
        }
    finally:
        con.close()


def semantic_rows(
    idx: Path, root_id: str | None, query: str, limit: int = 10
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    health = semantic_health(idx, root_id)
    if health["status"] != "current" or not root_id:
        return [], health
    model_name = configured_model()
    if model_name:
        qvec: Any = _embed([query], model_name)[0]
    else:
        qvec = vectorize(query)
        if not qvec:
            return [], health
    qterms = set(query_terms(query))
    con = sqlite3.connect(idx / "mimry.sqlite")
    con.row_factory = sqlite3.Row
    try:
        ensure_semantic_schema(con)
        rows = con.execute(
            "select chunk_id, rel_path, chunk_kind, chunk_text_preview, vector_json from"
            " semantic_chunks where root_id = ? order by rel_path, chunk_kind, chunk_id",
            (root_id,),
        ).fetchall()
    finally:
        con.close()

    hits: list[tuple[str, str, int, str]] = []
    for row in rows:
        if path_has_ignored_part(row["rel_path"]):
            continue
        try:
            vec = json.loads(row["vector_json"] or "{}")
        except json.JSONDecodeError:
            continue
        preview = row["chunk_text_preview"] or ""
        if model_name:
            # Dense vectors are almost never 0, so a floor replaces the
            # lexical fallback below.
            sim = _dot(qvec, vec)
            if sim < MODEL_FLOOR:
                continue
        else:
            sim = cosine_sparse(qvec, vec)
            if sim <= 0:
                # Hash vectors can collide; require weak lexical overlap
                # so every hit can be explained.
                overlap = qterms & set(_tokens(preview))
                if not overlap:
                    continue
                sim = min(0.12, 0.03 * len(overlap))
        score = int(round(sim * 100))
        if score <= 0:
            continue
        hits.append((row["rel_path"], row["chunk_kind"], score, preview))
    if model_name:
        hits = _cap_per_file(hits, MODEL_CHUNKS_PER_FILE)

    by_file: dict[str, dict[str, Any]] = {}
    for path, kind, score, preview in hits:
        label = {
            "path": "semantic path/symbol match",
            "symbol": "semantic path/symbol match",
            "adapter_fact": "semantic adapter-fact match",
            "doc_heading": "semantic adapter-fact match",
        }.get(kind, "semantic match")
        entry = by_file.setdefault(
            path,
            {
                "path": path,
                "score": 0,
                "reasons": set(),
                "semantic_details": [],
                "source": "semantic",
            },
        )
        entry["score"] += score
        entry["reasons"].add(label)
        if len(entry["semantic_details"]) < 2:
            entry["semantic_details"].append(f"{kind}: {preview[:160]}")

    results = []
    for entry in by_file.values():
        details = "; ".join(entry.pop("semantic_details"))
        reasons = ", ".join(sorted(entry.pop("reasons")))
        results.append({**entry, "reason": reasons, "details": details})
    return sorted(results, key=lambda r: (-r["score"], r["path"]))[:limit], health


def _cap_per_file(
    hits: list[tuple[str, str, int, str]], cap: int
) -> list[tuple[str, str, int, str]]:
    """Keep each file's best ``cap`` chunks, so many weak chunks cannot
    outvote one strong one."""
    order = sorted(range(len(hits)), key=lambda i: (-hits[i][2], hits[i][0], hits[i][1], i))
    taken: dict[str, int] = {}
    keep: set[int] = set()
    for i in order:
        path = hits[i][0]
        if taken.get(path, 0) < cap:
            taken[path] = taken.get(path, 0) + 1
            keep.add(i)
    return [hit for i, hit in enumerate(hits) if i in keep]


def _merge_rrf(
    base_rows: list[dict[str, Any]], semantic: list[dict[str, Any]], *, limit: int
) -> list[dict[str, Any]]:
    weight = float(os.environ.get(WEIGHT_ENV, "").strip() or 0.5)
    base_by_path: dict[str, dict[str, Any]] = {}
    base_rank: dict[str, int] = {}
    for rank, row in enumerate(base_rows, start=1):
        base_by_path.setdefault(row["path"], {**row})
        base_rank.setdefault(row["path"], rank)
    sem_by_path: dict[str, dict[str, Any]] = {}
    sem_rank: dict[str, int] = {}
    for rank, row in enumerate(semantic, start=1):
        sem_by_path.setdefault(row["path"], row)
        sem_rank.setdefault(row["path"], rank)

    fused: dict[str, float] = {}
    for path in base_rank.keys() | sem_rank.keys():
        value = 0.0
        if path in base_rank:
            value += 1 / (RRF_K + base_rank[path])
        if path in sem_rank:
            value += weight / (RRF_K + sem_rank[path])
        fused[path] = value
    order = sorted(fused, key=lambda path: (-fused[path], path))

    # Downstream code reads score, so keep the base scale: the i-th
    # fused row takes the i-th largest base score. Rows past the base
    # list step down one point each from its smallest score.
    base_scores = sorted((row["score"] for row in base_rows), reverse=True)
    floor_score = base_scores[-1] if base_scores else 1
    results: list[dict[str, Any]] = []
    for position, path in enumerate(order[:limit]):
        if position < len(base_scores):
            score = base_scores[position]
        else:
            score = max(1, floor_score - (position - len(base_scores) + 1))
        if path in base_by_path:
            row = base_by_path[path]
            if path in sem_by_path:
                sem = sem_by_path[path]
                row["reason"] = (
                    f"{row.get('reason', '')}, {sem['reason']}"
                    if row.get("reason")
                    else sem["reason"]
                )
                if sem.get("details") and not row.get("details"):
                    row["details"] = sem["details"]
        else:
            row = {**sem_by_path[path]}
        row["score"] = score
        results.append(row)
    return results


def merge_semantic_rows(
    base_rows: list[dict[str, Any]], semantic: list[dict[str, Any]], *, limit: int = 10
) -> list[dict[str, Any]]:
    if fusion_mode() == "rrf":
        return _merge_rrf(base_rows, semantic, limit=limit)
    by_path = {row["path"]: {**row} for row in base_rows}
    for row in semantic:
        if row["path"] in by_path:
            existing = by_path[row["path"]]
            boost = min(35, max(8, int(row["score"] * 0.35)))
            existing["score"] += boost
            existing["reason"] = (
                f"{existing.get('reason', '')}, {row['reason']}"
                if existing.get("reason")
                else row["reason"]
            )
            if row.get("details") and not existing.get("details"):
                existing["details"] = row["details"]
        else:
            # Keep semantic-only candidates useful but below strong
            # exact/graph evidence.
            by_path[row["path"]] = {**row, "score": min(row["score"], 80)}
    return sorted(by_path.values(), key=lambda r: (-r["score"], r["path"]))[:limit]
