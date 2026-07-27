from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .feedback import ensure_feedback_schema
from .semantic import ensure_semantic_schema
from .paths import idx_path, pointer_file, roots_file
from .security import sanitize_data


def load_pointer(root):
    p = pointer_file(root)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def save_pointer(root, ptr):
    pointer_file(root).parent.mkdir(parents=True, exist_ok=True)
    pointer_file(root).write_text(json.dumps(ptr, indent=2) + "\n", encoding="utf-8")


def register_root(ptr):
    p = roots_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    reg = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"roots": []}
    roots = [r for r in reg.get("roots", []) if r.get("rootId") != ptr["rootId"]]
    roots.append(ptr)
    reg["roots"] = sorted(roots, key=lambda r: r.get("rootPath", ""))
    p.write_text(json.dumps(reg, indent=2) + "\n", encoding="utf-8")


def connect(idx):
    idx.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(idx / "mimry.sqlite")
    con.executescript(
        """create table if not exists files(file_id text primary key, rel_path text, filename text, extension text, adapter text, parse_status text, content_hint text, metadata_text text); create table if not exists symbols(symbol_id text primary key, file_id text, name text, kind text, language text, line_start integer); create virtual table if not exists files_fts using fts5(file_id unindexed, rel_path, filename, extension, content_hint, metadata_text);"""
    )
    ensure_feedback_schema(con)
    ensure_semantic_schema(con)
    return con


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(sanitize_data(r), sort_keys=True) + "\n" for r in rows), encoding="utf-8")


def load_jsonl(path):
    return (
        [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        if Path(path).exists()
        else []
    )
