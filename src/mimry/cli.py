from __future__ import annotations

import argparse
import ast
import fnmatch
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .graphify_core import GraphifyCore

SCHEMA_VERSION = "0.1.0"
HEAVY_IGNORES = {"node_modules", "dist", "build", ".next", ".nuxt", "coverage", ".git", ".mimry", ".cache", ".expo", "target", "bin", "obj", "vendor", "__pycache__", ".pytest_cache", ".mypy_cache", ".venv", "venv"}
SENSITIVE_PATTERNS = [".env", ".env.*", "*.pem", "*.key", "id_rsa", "id_ed25519", "secrets.*", "credentials.*", "*.p12", "*.pfx"]
TEXT_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".css", ".html", ".sql", ".sh"}
IMPORT_RE = re.compile(r"(?:from|import)\s+['\"]([^'\"]+)['\"]|import\s+([\w./@-]+)")
EXPORT_RE = re.compile(r"export\s+(?:default\s+)?(?:function|class|const|let|var)?\s*([A-Za-z_$][\w$]*)?")


def now(): return datetime.now(timezone.utc).isoformat()
def cache_home(): return Path(os.environ.get("MIMRY_CACHE_HOME", Path.home()/".cache"/"mimry")).expanduser()
def roots_file(): return cache_home()/"roots.json"
def idx_path(root_id): return cache_home()/"indexes"/root_id
def mdir(root): return root/".mimry"
def pointer_file(root): return mdir(root)/"pointer.json"
def context_file(root): return mdir(root)/"context"/"latest.md"
def stable_id(*parts): return hashlib.sha256("::".join(map(str, parts)).encode()).hexdigest()[:24]

def safe_root(root: Path):
    r = root.resolve(); home = Path.home().resolve()
    if r == Path(r.anchor): raise ValueError(f"Refusing to index filesystem root: {r}")
    if r == home: raise ValueError(f"Refusing to index entire home without explicit target scope: {r}")

def load_pointer(root):
    p=pointer_file(root)
    return json.loads(p.read_text()) if p.exists() else None

def save_pointer(root, ptr):
    mdir(root).mkdir(parents=True, exist_ok=True)
    pointer_file(root).write_text(json.dumps(ptr, indent=2)+"\n")

def register_root(ptr):
    p=roots_file(); p.parent.mkdir(parents=True, exist_ok=True)
    reg=json.loads(p.read_text()) if p.exists() else {"roots": []}
    roots=[r for r in reg.get("roots", []) if r.get("rootId") != ptr["rootId"]]
    roots.append(ptr); reg["roots"]=sorted(roots, key=lambda r:r.get("rootPath", ""))
    p.write_text(json.dumps(reg, indent=2)+"\n")

def is_sensitive(path): return any(fnmatch.fnmatch(path.name, pat) for pat in SENSITIVE_PATTERNS)
def should_ignore(path, root):
    try: parts=path.relative_to(root).parts
    except ValueError: parts=path.parts
    return any(p in HEAVY_IGNORES for p in parts) or is_sensitive(path)
def is_text(path): return path.suffix.lower() in TEXT_EXTS

def text_hint(path, limit=12000):
    if not is_text(path): return ""
    try: text=path.read_bytes()[:limit].decode("utf-8", errors="ignore")
    except OSError: return ""
    return " ".join([l.strip() for l in text.splitlines() if l.strip()][:40])[:2000]

def sha(path):
    h=hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""): h.update(chunk)
    return h.hexdigest()

def scan(root):
    safe_root(root)
    for path in root.rglob("*"):
        if should_ignore(path, root) or not path.is_file() or path.is_symlink(): continue
        try:
            if path.stat().st_size > 1_000_000: continue
        except OSError: continue
        yield path

def file_record(path, root, adapter, status, hint):
    st=path.stat(); rel=path.relative_to(root).as_posix(); fid=stable_id(str(root.resolve()), rel)
    return {"file_id": fid, "path": str(path.resolve()), "rel_path": rel, "filename": path.name, "extension": path.suffix.lower(), "size": st.st_size, "mtime": st.st_mtime, "hash": sha(path), "adapter": adapter, "parse_status": status, "content_hint": hint, "metadata_text": f"{rel} {path.name} {path.suffix.lower()} {hint}"}

def adapt(path, root):
    ext=path.suffix.lower(); hint=text_hint(path); f=file_record(path, root, "generic", "ok", hint); symbols=[]; edges=[]; imports=[]; exports=[]
    if ext == ".py":
        f=file_record(path, root, "python-ast", "ok", hint)
        try:
            tree=ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    kind="class" if isinstance(node, ast.ClassDef) else "function"; sid=stable_id(f["file_id"], node.name, kind, getattr(node, "lineno", 0))
                    symbols.append({"symbol_id": sid, "file_id": f["file_id"], "name": node.name, "kind": kind, "language": "python", "exported": False, "line_start": getattr(node, "lineno", None), "line_end": getattr(node, "end_lineno", None)})
                    edges.append({"edge_id": stable_id(f["file_id"], sid, "defines"), "source_type": "file", "source_id": f["file_id"], "target_type": "symbol", "target_id": sid, "edge_type": "defines", "confidence": 1.0})
                elif isinstance(node, ast.Import): imports += [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module: imports.append(node.module)
        except SyntaxError as e: f=file_record(path, root, "python-ast", f"parse_error:{e.__class__.__name__}", hint)
    elif ext in {".js", ".jsx", ".ts", ".tsx"}:
        f=file_record(path, root, "js-ts-regex", "ok", hint); text=path.read_text(encoding="utf-8", errors="ignore") if is_text(path) else ""
        imports=[a or b for a,b in IMPORT_RE.findall(text) if a or b]; exports=[m for m in EXPORT_RE.findall(text) if m]
        for name in exports:
            sid=stable_id(f["file_id"], name, "export"); symbols.append({"symbol_id": sid, "file_id": f["file_id"], "name": name, "kind": "export", "language": ext.lstrip('.'), "exported": True, "line_start": None, "line_end": None})
            edges.append({"edge_id": stable_id(f["file_id"], sid, "defines"), "source_type": "file", "source_id": f["file_id"], "target_type": "symbol", "target_id": sid, "edge_type": "defines", "confidence": .8})
    return f, symbols, edges, sorted(set(imports)), sorted(set(exports))

def connect(idx):
    idx.mkdir(parents=True, exist_ok=True); con=sqlite3.connect(idx/"mimry.sqlite")
    con.executescript("""create table if not exists files(file_id text primary key, rel_path text, filename text, extension text, adapter text, parse_status text, content_hint text, metadata_text text); create table if not exists symbols(symbol_id text primary key, file_id text, name text, kind text, language text, line_start integer); create virtual table if not exists files_fts using fts5(file_id unindexed, rel_path, filename, extension, content_hint, metadata_text);""")
    return con

def write_jsonl(path, rows): path.write_text("".join(json.dumps(r, sort_keys=True)+"\n" for r in rows))
def load_jsonl(path): return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []

def write_index(root, ptr):
    files=[]; symbols=[]; edges=[]; imports={}; exports={}
    for p in scan(root):
        f, sy, ed, im, ex = adapt(p, root); files.append(f); symbols += sy; edges += ed
        if im: imports[f["rel_path"]]=im
        if ex: exports[f["rel_path"]]=ex
    ptr={**ptr, "lastIndexedAt": now()}; idx=idx_path(ptr["rootId"]); idx.mkdir(parents=True, exist_ok=True)
    graph=GraphifyCore().build_graph(files, symbols, edges)
    con=connect(idx)
    with con:
        con.execute("delete from files"); con.execute("delete from symbols"); con.execute("delete from files_fts")
        for f in files:
            con.execute("insert or replace into files values(?,?,?,?,?,?,?,?)", (f["file_id"], f["rel_path"], f["filename"], f["extension"], f["adapter"], f["parse_status"], f["content_hint"], f["metadata_text"]))
            con.execute("insert into files_fts values(?,?,?,?,?,?)", (f["file_id"], f["rel_path"], f["filename"], f["extension"], f["content_hint"], f["metadata_text"]))
        for s in symbols: con.execute("insert or replace into symbols values(?,?,?,?,?,?)", (s["symbol_id"], s["file_id"], s["name"], s["kind"], s["language"], s["line_start"]))
    con.close(); write_jsonl(idx/"files.jsonl", files); write_jsonl(idx/"symbols.jsonl", symbols); write_jsonl(idx/"imports.jsonl", [{"file":k,"imports":v} for k,v in imports.items()]); write_jsonl(idx/"exports.jsonl", [{"file":k,"exports":v} for k,v in exports.items()]); (idx/"dependencies.json").write_text(json.dumps(imports, indent=2)+"\n"); (idx/"graph.json").write_text(json.dumps(graph, indent=2)+"\n"); (idx/"file-hashes.json").write_text(json.dumps({f["rel_path"]:{"hash":f["hash"],"mtime":f["mtime"],"size":f["size"]} for f in files}, indent=2)+"\n")
    save_pointer(root, ptr); register_root(ptr); return {"files":len(files),"symbols":len(symbols),"edges":len(edges),"index":str(idx),"graph_engine":graph["engine"]}

def cmd_init(a):
    root=Path(a.root).resolve(); safe_root(root); root.mkdir(parents=True, exist_ok=True)
    if load_pointer(root): print("MIMRY is already initialized for this root."); return 0
    rid=str(uuid.uuid4()); ptr={"rootId":rid,"rootPath":str(root),"rootType":a.root_type,"indexPath":str(idx_path(rid)),"createdAt":now(),"lastIndexedAt":None,"schemaVersion":SCHEMA_VERSION}
    mdir(root).mkdir(parents=True, exist_ok=True); (mdir(root)/"context").mkdir(exist_ok=True); (mdir(root)/"config.toml").write_text('version = "0.1.0"\nroot_type = "repo"\nstore_full_text = false\n'); (mdir(root)/"AGENT_RULES.md").write_text("# MIMRY Agent Rules\n\nUse MIMRY before repeated grep or blind file reading.\n")
    save_pointer(root, ptr); register_root(ptr); print("MIMRY initialized.\nCreated:\n- .mimry/config.toml\n- .mimry/AGENT_RULES.md\n- .mimry/pointer.json\nNext: Run `mimry index`."); return 0

def require(root):
    ptr=load_pointer(root)
    if not ptr: raise SystemExit("MIMRY is not initialized here. Run `mimry init` first.")
    return ptr

def cmd_index(a):
    root=Path(a.root).resolve(); stats=write_index(root, require(root)); print(f"MIMRY indexing complete.\nIndexed files: {stats['files']}\nSymbols: {stats['symbols']}\nGraph edges: {stats['edges']}\nGraph engine: {stats['graph_engine']}\nIndex saved: {stats['index']}"); return 0

def cmd_status(a):
    root=Path(a.root).resolve(); ptr=load_pointer(root)
    if not ptr: print("MIMRY status\nInitialized: no\nRecommended: Run `mimry init`."); return 1
    idx=Path(ptr["indexPath"]); files=load_jsonl(idx/"files.jsonl"); symbols=load_jsonl(idx/"symbols.jsonl"); changed=[]; missing=[]
    for f in files:
        p=root/f["rel_path"]
        if not p.exists(): missing.append(f["rel_path"])
        elif p.stat().st_size != f["size"] or p.stat().st_mtime != f["mtime"]: changed.append(f["rel_path"])
    state="missing" if not (idx/"files.jsonl").exists() else ("stale" if changed or missing else "current")
    g=json.loads((idx/"graph.json").read_text()) if (idx/"graph.json").exists() else {"nodes":[],"edges":[]}
    print(f"MIMRY status\nRoot: {root}\nInitialized: yes\nIndex: {state}\nLast indexed: {ptr.get('lastIndexedAt') or 'never'}\nFiles indexed: {len(files)}\nSymbols indexed: {len(symbols)}\nGraph nodes/edges: {len(g.get('nodes', []))}/{len(g.get('edges', []))}\nChanged files: {len(changed)}\nDeleted files: {len(missing)}\nIndex path: {idx}")
    if state == "stale": print("Recommended: Run `mimry reindex`.")
    return 0 if state == "current" else 2

def score(f, q):
    terms=[t.lower() for t in q.replace("_"," ").replace("-"," ").split() if t]; h={"filename":f["filename"].lower(),"path":f["rel_path"].lower(),"content hint":f.get("content_hint","").lower(),"metadata":f.get("metadata_text","").lower()}; s=0; reasons=[]
    for term in terms:
        for label,text in h.items():
            if term in text: s += {"filename":30,"path":20,"content hint":10,"metadata":6}[label]; reasons.append(f"{label} match") if f"{label} match" not in reasons else None
    if s and f["adapter"] != "generic": s += 8; reasons.append(f["adapter"]+" adapter")
    return s, reasons

def find_rows(idx, q, limit=10, graph=False):
    rows=[]; clusters={}
    if graph and (idx/"graph.json").exists(): clusters=json.loads((idx/"graph.json").read_text()).get("clusters", {})
    for f in load_jsonl(idx/"files.jsonl"):
        s, rs=score(f,q)
        if s:
            folder=f["rel_path"].rsplit("/",1)[0] if "/" in f["rel_path"] else "."
            if graph and folder in clusters: s += 5; rs.append("Graphify cluster relationship")
            rows.append({"path":f["rel_path"],"score":s,"reason":", ".join(rs)})
    return sorted(rows, key=lambda r:(-r["score"], r["path"]))[:limit]

def print_rows(title, rows):
    print(title)
    for i,r in enumerate(rows,1): print(f"{i}. {r['path']}\n   Score: {r['score']}\n   Reason: {r['reason']}")

def cmd_find(a): ptr=require(Path(a.root).resolve()); print_rows(f"Search results for: {a.query}", find_rows(Path(ptr["indexPath"]), a.query, a.limit)); return 0
def cmd_related(a): ptr=require(Path(a.root).resolve()); print_rows(f"Related files for: {a.query}", find_rows(Path(ptr["indexPath"]), a.query, a.limit, True)); return 0
def cmd_symbol(a):
    ptr=require(Path(a.root).resolve()); print(f"Symbol search: {a.name}"); files={f["file_id"]:f for f in load_jsonl(Path(ptr["indexPath"])/"files.jsonl")}
    for i,s in enumerate([s for s in load_jsonl(Path(ptr["indexPath"])/"symbols.jsonl") if a.name.lower() in s["name"].lower()],1): print(f"{i}. {s['name']} ({s['kind']}, {s['language']}) — {files.get(s['file_id'],{}).get('rel_path', s['file_id'])}:{s.get('line_start') or ''}")
    return 0

def cmd_context(a):
    root=Path(a.root).resolve(); ptr=require(root); rows=find_rows(Path(ptr["indexPath"]), a.query, 8, True)
    lines=["# MIMRY Context Pack","","## Query",a.query,"","## Index Status","Generated from current local MIMRY index.","","## Summary",f"MIMRY found {len(rows)} relevant file(s) using metadata and Graphify cluster signals.","","## Relevant Files"]
    for i,r in enumerate(rows,1): lines += [f"### {i}. `{r['path']}`", f"Score: {r['score']}", f"Reason: {r['reason']}", ""]
    lines += ["## Relevant Symbols / Entities","Use `mimry symbol <name>` for concrete symbols.","","## Relationship Paths","Early MVP uses file definitions and Graphify folder clusters.","","## Suggested Reading Order"] + [f"{i}. `{r['path']}`" for i,r in enumerate(rows,1)] + ["","## Risk Notes","- Open source files before editing.","- Re-run `mimry reindex` after changes.","","## Suggested Verification","- Run project tests/typecheck/build for affected files.","","## Source of Truth Reminder","Original files, tests, builds, and human verification remain final truth.",""]
    context_file(root).parent.mkdir(parents=True, exist_ok=True); context_file(root).write_text("\n".join(lines)); print(f"Context pack generated.\nOutput: {context_file(root)}"); return 0

def cmd_roots(a):
    reg=json.loads(roots_file().read_text()) if roots_file().exists() else {"roots": []}; print("MIMRY roots"); [print(f"- {r['rootId']} {r['rootType']} {r['rootPath']} -> {r['indexPath']}") for r in reg.get("roots", [])]; return 0

def cmd_cache_wipe(a):
    if a.all: shutil.rmtree(cache_home(), ignore_errors=True); print(f"Wiped all MIMRY cache: {cache_home()}"); return 0
    ptr=require(Path(a.root).resolve()); shutil.rmtree(Path(ptr["indexPath"]), ignore_errors=True); print(f"Wiped current root cache: {ptr['indexPath']}"); return 0

def repo_root():
    return Path(__file__).resolve().parents[2]

def graphify_vendor_path():
    return repo_root() / "vendor" / "graphify"

def graphify_commit():
    vendor = graphify_vendor_path()
    if not vendor.exists():
        return "missing"
    try:
        return subprocess.check_output(["git", "-C", str(vendor), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"

def graphify_output_dir(root: Path):
    return mdir(root) / "graphify"

def cmd_graphify_status(a):
    vendor = graphify_vendor_path()
    print("Graphify status")
    print(f"Vendor path: {vendor.relative_to(repo_root()) if vendor.exists() else vendor}")
    print(f"Vendor exists: {vendor.exists()}")
    print(f"Pinned commit: {graphify_commit()}")
    print("Allowed MIMRY wrapper commands: status, build")
    print("Blocked by design: graphify install, graphify hook, provider config, assistant integrations")
    return 0 if vendor.exists() else 2

def cmd_graphify_build(a):
    root = Path(a.root).resolve()
    safe_root(root)
    out = graphify_output_dir(root)
    vendor = graphify_vendor_path()
    cmd = [sys.executable, "-m", "graphify", "update", str(root)]
    env = {**os.environ, "PYTHONPATH": str(vendor), "GRAPHIFY_OUT": str(out)}
    if a.dry_run or not a.execute:
        print("MIMRY Graphify build: DRY RUN")
        print(f"Root: {root}")
        print(f"Vendor: {vendor}")
        print(f"Pinned commit: {graphify_commit()}")
        print(f"GRAPHIFY_OUT={out}")
        print("Command: python -m graphify update <root>")
        print("To execute: mimry --root <root> graphify build --execute")
        return 0
    if not vendor.exists():
        print("Graphify vendor submodule is missing. Run `git submodule update --init --recursive`.", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    print(f"Running safe Graphify build with GRAPHIFY_OUT={out}")
    res = subprocess.run(cmd, cwd=root, env=env, text=True, capture_output=True, check=False)
    if res.stdout.strip(): print(res.stdout.strip())
    if res.stderr.strip(): print(res.stderr.strip(), file=sys.stderr)
    if res.returncode != 0:
        print(f"Graphify build failed with exit {res.returncode}", file=sys.stderr)
        return res.returncode
    print(f"Graphify output: {out}")
    return 0

def cmd_graphify(a):
    if a.graphify_command == "status": return cmd_graphify_status(a)
    if a.graphify_command == "build": return cmd_graphify_build(a)
    raise SystemExit("unknown graphify command")

def build_parser():
    p=argparse.ArgumentParser(prog="mimry"); p.add_argument("--root", default="."); sub=p.add_subparsers(dest="command", required=True)
    s=sub.add_parser("init"); s.add_argument("--root-type", default="repo"); s.set_defaults(func=cmd_init)
    sub.add_parser("index").set_defaults(func=cmd_index); sub.add_parser("reindex").set_defaults(func=cmd_index); sub.add_parser("status").set_defaults(func=cmd_status)
    s=sub.add_parser("find"); s.add_argument("query"); s.add_argument("--limit", type=int, default=10); s.set_defaults(func=cmd_find)
    s=sub.add_parser("related"); s.add_argument("query"); s.add_argument("--limit", type=int, default=10); s.set_defaults(func=cmd_related)
    s=sub.add_parser("symbol"); s.add_argument("name"); s.set_defaults(func=cmd_symbol)
    s=sub.add_parser("context"); s.add_argument("query"); s.set_defaults(func=cmd_context)
    sub.add_parser("roots").set_defaults(func=cmd_roots); cache=sub.add_parser("cache"); cs=cache.add_subparsers(required=True); w=cs.add_parser("wipe"); w.add_argument("--all", action="store_true"); w.add_argument("--current", action="store_true"); w.set_defaults(func=cmd_cache_wipe)
    g=sub.add_parser("graphify", help="Safe MIMRY-owned wrapper around pinned Graphify")
    gs=g.add_subparsers(dest="graphify_command", required=True)
    gs.add_parser("status").set_defaults(func=cmd_graphify)
    gb=gs.add_parser("build")
    gb.add_argument("--dry-run", action="store_true", help="Show the safe Graphify command without running it")
    gb.add_argument("--execute", action="store_true", help="Run the safe local Graphify build with output under .mimry/graphify")
    gb.set_defaults(func=cmd_graphify)
    return p

def main(argv=None):
    a=build_parser().parse_args(argv); return a.func(a)

if __name__ == "__main__": sys.exit(main())
