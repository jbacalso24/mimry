from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from pathlib import Path
from typing import Any

from .intent import query_terms
from .paths import now, stable_id
from .security import contains_sensitive_text, path_has_ignored_part, redact_sensitive_text
from .state import semantic_rows_checksum

SEMANTIC_BACKEND = "local-hash-v1"
SEMANTIC_SCHEMA_VERSION = "0.1.0"
VECTOR_DIMS = 256
MAX_PREVIEW = 500

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
    con.execute("create index if not exists semantic_chunks_root_path_idx on semantic_chunks(root_id, rel_path)")
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
        counts[_hash_index(token)] = counts.get(_hash_index(token), 0.0) + 1.0
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


def _bounded_preview(text: str) -> str:
    return " ".join(text.split())[:MAX_PREVIEW]


def _chunk_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def _chunk(root_id: str, file_id: str, rel_path: str, kind: str, text: str, created_at: str) -> dict[str, Any] | None:
    if contains_sensitive_text(text) or contains_sensitive_text(rel_path):
        return None
    text = redact_sensitive_text(text)
    preview = _bounded_preview(text)
    vector = vectorize(text)
    if not preview or not vector:
        return None
    return {
        "chunk_id": stable_id(root_id, rel_path, kind, _chunk_text_hash(text)),
        "root_id": root_id,
        "file_id": file_id,
        "rel_path": rel_path,
        "chunk_kind": kind,
        "chunk_text_hash": _chunk_text_hash(text),
        "chunk_text_preview": preview,
        "vector": vector,
        "model_name": SEMANTIC_BACKEND,
        "backend_name": SEMANTIC_BACKEND,
        "created_at": created_at,
        "schema_version": SEMANTIC_SCHEMA_VERSION,
    }


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
    symbol_names = " ".join(sym.get("name", "") for sym in symbols if sym.get("file_id") == file_id)
    if symbol_names:
        candidates.append(("symbol", f"{rel_path} {symbol_names}"))
    if file_rec.get("extension") in {".md", ".mdx", ".rst"}:
        headings = " ".join(part for part in metadata.split(" | ") if "markdown headings" in part.lower())
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
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_semantic_index(idx: Path, root_id: str, generation_id: str | None = None) -> dict[str, Any]:
    files = _load_jsonl(idx / "files.jsonl")
    symbols = _load_jsonl(idx / "symbols.jsonl")
    created_at = now()
    chunks: list[dict[str, Any]] = []
    for file_rec in files:
        chunks.extend(_semantic_chunks_for_file(root_id, file_rec, symbols, created_at))

    con = sqlite3.connect(idx / "mimry.sqlite")
    try:
        ensure_semantic_schema(con)
        generation_columns = {row[1] for row in con.execute("pragma table_info(index_generation)")}
        if "semantic_checksum" not in generation_columns:
            con.execute("alter table index_generation add column semantic_checksum text")
        if generation_id is None:
            generation_row = con.execute("select generation_id from index_generation limit 1").fetchone()
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
            for chunk, checksum_row in zip(chunks, checksum_rows, strict=True):
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
                    SEMANTIC_BACKEND,
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
        "backend": SEMANTIC_BACKEND,
        "chunks": len(chunks),
        "indexed_at": created_at,
        "generation": generation_id,
        "checksum": content_checksum,
    }


def semantic_health(idx: Path, root_id: str | None, expected_files: int | None = None) -> dict[str, Any]:
    if not root_id or not (idx / "mimry.sqlite").exists():
        return {"status": "missing", "backend": SEMANTIC_BACKEND, "chunks": 0, "indexed_at": None}
    con = sqlite3.connect(idx / "mimry.sqlite")
    con.row_factory = sqlite3.Row
    try:
        ensure_semantic_schema(con)
        row = con.execute("select * from semantic_metadata where root_id = ?", (root_id,)).fetchone()
        if not row:
            return {"status": "missing", "backend": SEMANTIC_BACKEND, "chunks": 0, "indexed_at": None}
        chunks = int(row["chunk_count"] or 0)
        policy_excluded_chunks = sum(
            1
            for chunk in con.execute("select rel_path from semantic_chunks where root_id = ?", (root_id,))
            if path_has_ignored_part(chunk["rel_path"])
        )
        status = "current" if chunks > 0 else "missing"
        if policy_excluded_chunks or (expected_files is not None and expected_files > 0 and chunks < expected_files):
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
    qvec = vectorize(query)
    qterms = set(query_terms(query))
    if not qvec:
        return [], health
    con = sqlite3.connect(idx / "mimry.sqlite")
    con.row_factory = sqlite3.Row
    try:
        ensure_semantic_schema(con)
        rows = con.execute(
            "select rel_path, chunk_kind, chunk_text_preview, vector_json from semantic_chunks where root_id = ?",
            (root_id,),
        ).fetchall()
    finally:
        con.close()

    by_file: dict[str, dict[str, Any]] = {}
    for row in rows:
        if path_has_ignored_part(row["rel_path"]):
            continue
        try:
            vec = json.loads(row["vector_json"] or "{}")
        except json.JSONDecodeError:
            continue
        sim = cosine_sparse(qvec, vec)
        preview = row["chunk_text_preview"] or ""
        if sim <= 0:
            # Deterministic hash vectors can collide; require at least weak lexical fallback for explainability.
            overlap = qterms & set(_tokens(preview))
            if not overlap:
                continue
            sim = min(0.12, 0.03 * len(overlap))
        score = int(round(sim * 100))
        if score <= 0:
            continue
        path = row["rel_path"]
        kind = row["chunk_kind"]
        label = {
            "path": "semantic path/symbol match",
            "symbol": "semantic path/symbol match",
            "adapter_fact": "semantic adapter-fact match",
            "doc_heading": "semantic adapter-fact match",
        }.get(kind, "semantic match")
        entry = by_file.setdefault(
            path,
            {"path": path, "score": 0, "reasons": set(), "semantic_details": [], "source": "semantic"},
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


def merge_semantic_rows(
    base_rows: list[dict[str, Any]], semantic: list[dict[str, Any]], *, limit: int = 10
) -> list[dict[str, Any]]:
    by_path = {row["path"]: {**row} for row in base_rows}
    for row in semantic:
        if row["path"] in by_path:
            existing = by_path[row["path"]]
            boost = min(35, max(8, int(row["score"] * 0.35)))
            existing["score"] += boost
            existing["reason"] = (
                f"{existing.get('reason', '')}, {row['reason']}" if existing.get("reason") else row["reason"]
            )
            if row.get("details") and not existing.get("details"):
                existing["details"] = row["details"]
        else:
            # Keep semantic-only candidates useful but below strong exact/graph evidence.
            by_path[row["path"]] = {**row, "score": min(row["score"], 80)}
    return sorted(by_path.values(), key=lambda r: (-r["score"], r["path"]))[:limit]
