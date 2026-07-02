from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .paths import idx_path, pointer_file, roots_file

def load_pointer(root):
    p=pointer_file(root)
    return json.loads(p.read_text()) if p.exists() else None

def save_pointer(root, ptr):
    pointer_file(root).parent.mkdir(parents=True, exist_ok=True)
    pointer_file(root).write_text(json.dumps(ptr, indent=2)+"\n")

def register_root(ptr):
    p=roots_file(); p.parent.mkdir(parents=True, exist_ok=True)
    reg=json.loads(p.read_text()) if p.exists() else {"roots": []}
    roots=[r for r in reg.get("roots", []) if r.get("rootId") != ptr["rootId"]]
    roots.append(ptr); reg["roots"]=sorted(roots, key=lambda r:r.get("rootPath", ""))
    p.write_text(json.dumps(reg, indent=2)+"\n")

def connect(idx):
    idx.mkdir(parents=True, exist_ok=True); con=sqlite3.connect(idx/"mimry.sqlite")
    con.executescript("""create table if not exists files(file_id text primary key, rel_path text, filename text, extension text, adapter text, parse_status text, content_hint text, metadata_text text); create table if not exists symbols(symbol_id text primary key, file_id text, name text, kind text, language text, line_start integer); create virtual table if not exists files_fts using fts5(file_id unindexed, rel_path, filename, extension, content_hint, metadata_text);""")
    return con

def write_jsonl(path, rows): path.write_text("".join(json.dumps(r, sort_keys=True)+"\n" for r in rows))
def load_jsonl(path): return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if Path(path).exists() else []
