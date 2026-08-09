"""Canonical semantic digest.

MIMRY's state has two halves and only one of them is reproducible.

**Canonical semantic state** must be identical for the same repository content
and configuration, no matter where the repository is checked out, what order
the filesystem hands back its entries, what ``PYTHONHASHSEED`` is set to, or
which platform ran the index. It is what this module hashes:

* relative file identities, adapters, and parse status
* symbols with their line pointers
* imports and exports
* graph nodes, edges, and community assignments
* folder clusters
* semantic chunk identities, kinds, and previews
* the list of paths MIMRY refused to index

**Operational/provenance envelope** may legitimately vary between two runs of
the same content and is therefore excluded:

* timestamps (``created_at``, ``indexed_at``, ``lastIndexedAt``)
* generation UUIDs
* absolute cache paths and local root paths
* ``mtime``, which exists only for cache invalidation
* feedback event IDs and any locally recorded feedback
* process/runtime metadata
* the physical byte layout of the SQLite file

Hashing raw SQLite bytes or the generation manifest would fold that envelope
into the result and report a false difference, so this reads the semantic rows
back out and normalizes them instead.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

DIGEST_VERSION = "1"

# A masked operational value must not swallow the token that follows it. The
# root `/w/checkout` appears inside `/w/checkout-plan.md`, which is a different
# path and canonical content; requiring the next character to be outside an
# identifier keeps that intact while still matching `/w/checkout/src` and
# `` `/w/checkout` ``.
_VALUE_BOUNDARY = r"(?![A-Za-z0-9_.\-])"

# Dropped from every file record before hashing: an absolute checkout path and a
# filesystem timestamp are provenance, not meaning.
_OPERATIONAL_FILE_FIELDS = frozenset({"path", "mtime"})

# Dropped from every semantic chunk row: when it was built and under which
# generation UUID says nothing about what it means.
_OPERATIONAL_CHUNK_FIELDS = frozenset({"created_at", "generation_id"})


def _canonical(value: Any) -> Any:
    """Recursively sort mappings so key insertion order cannot reach the hash."""
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def _load_jsonl(path: Path, drop: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [{k: v for k, v in row.items() if k not in drop} for row in rows]


def _load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _semantic_chunks(idx: Path, root_id: str | None) -> list[dict[str, Any]]:
    """Read semantic chunks back as normalized rows.

    Read through SQL rather than over the file so the physical page layout --
    which differs between a freshly built database and one seeded from a
    previous generation -- cannot change the result.
    """
    db = idx / "mimry.sqlite"
    if not db.is_file():
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        if root_id:
            cursor = con.execute(
                "select * from semantic_chunks where root_id = ? order by rel_path, chunk_kind, chunk_id",
                (root_id,),
            )
        else:
            cursor = con.execute("select * from semantic_chunks order by rel_path, chunk_kind, chunk_id")
        rows = [
            {key: row[key] for key in row.keys() if key not in _OPERATIONAL_CHUNK_FIELDS and key != "root_id"}
            for row in cursor
        ]
    except sqlite3.Error:
        return []
    finally:
        con.close()
    return rows


def canonical_state(idx: Path, root_id: str | None = None) -> dict[str, Any]:
    """Collect the reproducible half of an index generation.

    ``idx`` is a generation directory (the ``indexPath`` recorded in the root
    pointer). The result contains no absolute path, timestamp, or UUID.
    """
    idx = Path(idx)
    graph = _load_json(idx / "graph.json", {})
    return _canonical(
        {
            "digestVersion": DIGEST_VERSION,
            "files": sorted(
                _load_jsonl(idx / "files.jsonl", _OPERATIONAL_FILE_FIELDS),
                key=lambda row: str(row.get("rel_path", "")),
            ),
            "symbols": sorted(
                _load_jsonl(idx / "symbols.jsonl"),
                key=lambda row: (str(row.get("file_id", "")), str(row.get("symbol_id", ""))),
            ),
            "imports": sorted(_load_jsonl(idx / "imports.jsonl"), key=lambda row: str(row.get("file", ""))),
            "exports": sorted(_load_jsonl(idx / "exports.jsonl"), key=lambda row: str(row.get("file", ""))),
            "dependencies": _load_json(idx / "dependencies.json", {}),
            "graph": {
                "engine": graph.get("engine"),
                "nodes": graph.get("nodes", []),
                "edges": graph.get("edges", []),
                "clusters": graph.get("clusters", {}),
            },
            "semanticChunks": _semantic_chunks(idx, root_id),
            "unindexable": sorted(_load_json(idx / "unindexable.json", [])),
        }
    )


def canonical_digest(idx: Path, root_id: str | None = None) -> str:
    """SHA-256 over the canonical semantic state of one index generation."""
    blob = json.dumps(canonical_state(idx, root_id), sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def normalize_context(
    text: str,
    *,
    root: str | Path | None = None,
    index_path: str | Path | None = None,
    generation_id: str | None = None,
    indexed_at: str | None = None,
) -> str:
    """Blank the operational envelope out of a rendered context pack.

    Only the four values named by the caller are masked, and only where they
    appear verbatim. Everything else on the line survives, because everything
    else is canonical: file and symbol counts, evidence ordering, reason
    labels, risk notes, and content hashes.

    This deliberately does not pattern-match "anything that looks like a
    timestamp" or "any 32-character hex token". MIMRY's own risk notes mention
    generated paths, and content hashes are 32-hex-adjacent; masking by shape
    rather than by value hid real semantic drift.

    Substitutions run longest-value-first so the index path -- which embeds the
    generation id -- is replaced whole instead of leaving a stub behind.
    """
    replacements: list[tuple[str, str]] = []
    for value, token in (
        (index_path, "<indexpath>"),
        (root, "<root>"),
        (generation_id, "<generation>"),
        (indexed_at, "<timestamp>"),
    ):
        if not value:
            continue
        candidates = {str(value)}
        if isinstance(value, Path) or token in ("<root>", "<indexpath>"):
            # A path can reach the pack in native or POSIX form depending on
            # which layer rendered it; mask both spellings of the same value.
            candidates.add(Path(str(value)).as_posix())
        for candidate in candidates:
            if candidate:
                replacements.append((candidate, token))

    replacements.sort(key=lambda pair: len(pair[0]), reverse=True)

    lines = []
    for line in text.splitlines():
        if not line.strip():
            continue
        for candidate, token in replacements:
            line = re.sub(re.escape(candidate) + _VALUE_BOUNDARY, token, line)
        lines.append(line)
    return "\n".join(lines)
