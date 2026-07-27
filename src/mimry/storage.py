from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from .feedback import ensure_feedback_schema
from .paths import pointer_file, roots_file
from .semantic import ensure_semantic_schema
from .state import (
    StateCorruptionError,
    atomic_write_json,
    atomic_write_text,
    exclusive_file_lock,
    load_json_state,
    validate_generation,
)


def _recovery_notice(path: Path) -> None:
    print(
        f"Recovered corrupt MIMRY state at {path} from last-known-good backup {path.name}.bak.",
        file=sys.stderr,
    )


def _pointer_lock(root: Path) -> Path:
    p = pointer_file(root)
    return p.with_name(f"{p.name}.lock")


def _load_pointer_unlocked(root, *, validate_active_generation: bool = True):
    p = pointer_file(root)
    payload, recovered = load_json_state(
        p,
        default=None,
        recover_backup=True,
        validator=lambda value: (
            isinstance(value, dict)
            and all(isinstance(value.get(key), str) and value[key] for key in ("rootId", "rootPath", "indexPath"))
        ),
        expected="a pointer object with non-empty rootId, rootPath, and indexPath strings",
    )
    if recovered:
        _recovery_notice(p)
    if payload and validate_active_generation:
        validate_generation(payload)
    return payload


def load_pointer(root, *, validate_active_generation: bool = True):
    with exclusive_file_lock(_pointer_lock(Path(root))):
        return _load_pointer_unlocked(root, validate_active_generation=validate_active_generation)


def save_pointer(root, ptr):
    p = pointer_file(root)
    with exclusive_file_lock(_pointer_lock(Path(root))):
        atomic_write_json(p, ptr, keep_backup=True)


def _canonical_root_path(value: Any) -> str:
    path = Path(str(value)).expanduser().resolve(strict=False)
    return os.path.normcase(str(path))


def _entry_recency(entry: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(entry.get("lastIndexedAt") or ""),
        str(entry.get("createdAt") or ""),
        str(entry.get("rootId") or ""),
    )


def _dedupe_roots(roots: Any, *, preferred: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    by_path: dict[str, dict[str, Any]] = {}
    preferred_key = _canonical_root_path(preferred["rootPath"]) if preferred else ""
    for raw in roots if isinstance(roots, list) else []:
        if not isinstance(raw, dict) or not raw.get("rootPath") or not raw.get("rootId"):
            continue
        entry = dict(raw)
        key = _canonical_root_path(entry["rootPath"])
        entry["rootPath"] = str(Path(entry["rootPath"]).expanduser().resolve(strict=False))
        current = by_path.get(key)
        if current is None or _entry_recency(entry) > _entry_recency(current):
            by_path[key] = entry
    if preferred is not None:
        entry = dict(preferred)
        entry["rootPath"] = str(Path(entry["rootPath"]).expanduser().resolve(strict=False))
        by_path[preferred_key] = entry
    return sorted(by_path.values(), key=lambda entry: os.path.normcase(entry["rootPath"]))


def _load_root_registry_unlocked() -> tuple[dict[str, Any], bool]:
    p = roots_file()
    payload, recovered = load_json_state(
        p,
        default={"roots": []},
        recover_backup=True,
        validator=lambda value: isinstance(value, dict) and isinstance(value.get("roots", []), list),
        expected="an object containing a roots array",
    )
    if recovered:
        _recovery_notice(p)

    deduped = _dedupe_roots(payload.get("roots", []))
    repaired = deduped != payload.get("roots", [])
    return {**payload, "roots": deduped}, repaired


def load_root_registry(*, repair: bool = True) -> dict[str, Any]:
    p = roots_file()
    with exclusive_file_lock(p.with_name(f"{p.name}.lock")):
        registry, needs_repair = _load_root_registry_unlocked()
        if repair and needs_repair:
            atomic_write_json(p, registry, keep_backup=True)
        return registry


def register_root(ptr):
    p = roots_file()
    with exclusive_file_lock(p.with_name(f"{p.name}.lock")):
        registry, _ = _load_root_registry_unlocked()
        registry["roots"] = _dedupe_roots(registry.get("roots", []), preferred=ptr)
        atomic_write_json(p, registry, keep_backup=True)


def connect(idx):
    idx.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(idx / "mimry.sqlite")
    con.executescript(
        """create table if not exists files(file_id text primary key, rel_path text, filename text, extension text, adapter text, parse_status text, content_hint text, metadata_text text); create table if not exists symbols(symbol_id text primary key, file_id text, name text, kind text, language text, line_start integer); create virtual table if not exists files_fts using fts5(file_id unindexed, rel_path, filename, extension, content_hint, metadata_text); create table if not exists index_generation(generation_id text primary key, created_at text not null, semantic_checksum text);"""
    )
    generation_columns = {row[1] for row in con.execute("pragma table_info(index_generation)")}
    if "semantic_checksum" not in generation_columns:
        con.execute("alter table index_generation add column semantic_checksum text")
    ensure_feedback_schema(con)
    ensure_semantic_schema(con)
    return con


def write_jsonl(path, rows):
    atomic_write_text(path, "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))


def load_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except json.JSONDecodeError as exc:
        raise StateCorruptionError(path, f"invalid JSONL at line {exc.lineno}, column {exc.colno}") from exc
    except UnicodeError as exc:
        raise StateCorruptionError(path, "file is not valid UTF-8") from exc
