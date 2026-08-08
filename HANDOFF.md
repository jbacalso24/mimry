# MIMRY core engine — handoff state

Branch: `dev` (15 commits, **not pushed, not merged**). Working tree clean.

## What was done

Replaced the vendored Graphify dependency (115,306 lines) with MIMRY's own graph
engine at `src/mimry/core/` (~2,900 lines). Measured against a recorded baseline
on `benchmarks/cases.v1.json`, nine cases, identical fixture:

| metric | no-graph | graphify | mimry.core | delta vs graphify |
|---|---|---|---|---|
| ndcg@5 | 0.6252 | 0.6300 | **0.7232** | +0.0933 |
| recall@5 | 0.8125 | 0.8542 | **0.9375** | +0.0833 |
| primary_hit@3 | 0.7500 | 0.5000 | **0.7500** | +0.2500 |
| decoy_rate@5 | 0.1622 | 0.1351 | **0.0811** | −0.0541 |
| setup total | — | 1825ms | **975ms** | −47% |

Baselines are committed and cannot be regenerated (the dependency is gone):
- `benchmarks/baseline.graphify.json` — Graphify at pinned commit 44c0a5e
- `benchmarks/baseline.nograph.json` — control, graph disabled
- `benchmarks/result.core.json` — current engine

## Engine layout

```
src/mimry/core/
  build.py      GraphEngine.build_graph -> {engine, nodes, edges, clusters}
  languages.py  extension -> tree-sitter grammar + node-type table
  resolve.py    resolve_imports / resolve_calls / resolve_doc_links / resolve_table_refs
  cluster.py    deterministic label propagation
  report.py     GRAPH_REPORT.md + manifest.json
  documents.py  .docx/.xlsx text extraction (zipfile + regex, no XML parser)
  artifacts.py  readers/queries (was graphify_artifacts.py)
```

Each of build/languages/resolve/cluster/report/documents has a runnable
`__main__` self-check that prints `OK`.

Edge types: `defines`, `imports` (file→file), `calls` (symbol→symbol),
`references` (doc→file, and code/doc→sql_table symbol).
Languages: Python, JS, JSX, TS, TSX, Go, Rust, C#.

## Contracts that will silently break if violated

1. **Edges use `source`/`target`, never `from`/`to`.** `core/artifacts.py::graph_rows`
   counts degree via `edge.get("source")` only; the old `from`/`to` spelling scored
   zero topology boost with no error anywhere. `freshness.py` filters on the same keys.
2. **Node ids are `file:{file_id}` / `symbol:{symbol_id}`.** `freshness.py` builds an
   allowed-id set from exactly those prefixes; any deviation discards every node.
3. **`graph.json` must be byte-identical across rebuilds** — `state.py::generation_manifest`
   checksums it and the state-recovery tests assert reproducibility. Clustering is
   deterministic (sorted iteration, lexicographic tie-break, no `random`); verified
   stable across five `PYTHONHASHSEED` values.
4. **`clusters` is still consumed** by `search.py` and `freshness.py`. Do not drop it.
5. **Resolvers decline ambiguity.** Two candidates → emit nothing. Never guess.
6. **All source is ASCII** — `tests/test_encoding.py` enforces it and a cp1252 Windows
   console raises on non-ASCII output.
7. **Use `posixpath`, never `os.path`,** for `rel_path` joins. `os.path.normpath`
   returns backslashes on Windows and would resolve nothing there while passing on Linux.

## Environment gotchas on this machine

- Python: `.venv/Scripts/python.exe`
- **pytest needs `--basetemp="C:/tmp/<unique>"`.** The default temp dir
  (`AppData/Local/Temp/pytest-of-j.bacalso`) is permission-locked and produces ~137
  spurious setup errors. Use a UNIQUE dir per run — concurrent runs sharing one
  basetemp lock each other and produce fake failures.
- tree-sitter here uses a **method-style API**: `parse(str)`, `tree.root_node()`,
  `node.kind()`, `node.child(i)`. `node.type` does not exist. Idiomatic tree-sitter
  code raises.
- Git Bash `/tmp` maps to `AppData/Local/Temp`, but Python reads `/tmp` as `C:\tmp`.
  Use explicit absolute paths.

## Current status

- **154 tests passing, 0 failing** on Windows.
- `ruff check` and `ruff format --check` clean.
- CI matrix now includes `windows-latest` alongside `ubuntu-latest` and `macos-latest`.
- Two benchmark checks still fail by design: `primary_hit_at_3` (0.75 vs 0.80 gate)
  and `context_token_proxy_max` (more graph evidence → larger context packs).

## THE OPEN ITEM — Linux and macOS

**Never verified.** All work happened on Windows 11. CI has never run because
nothing is pushed. Every platform fix is capability-guarded and should be a no-op
on POSIX, but that is reasoning, not evidence.

Windows fixes made, each of which must stay POSIX-neutral:

| file | fix |
|---|---|
| `paths.py::cache_home` | resolve `MIMRY_CACHE_HOME` before evaluating `Path.home()` (was an eager `get()` default) |
| `state.py::fsync_tree` | open `"rb+"`; Windows rejects fsync on a read-only fd |
| `state.py::file_lock` | holder metadata at `LOCK_METADATA_OFFSET = 1`; byte 0 is the Windows lock byte and msvcrt locks block reads |
| `security.py::stat_identity` | drop `st_ctime_ns` on nt only — Windows reports creation time at different precision from `fstat` vs `lstat`; **POSIX keeps it** |
| `commands.py`, `indexer.py` | `stdin=subprocess.DEVNULL` on git calls — under MCP stdio, stdin IS the JSON-RPC channel |
| `installer.py::_is_mimry_hook` | match the marker, not the literal `"mimry hook-check"` (Windows resolves `mimry.EXE`) |
| `agent_integration.py::_client_env` | set `USERPROFILE`/`HOMEDRIVE`/`HOMEPATH`; `ntpath.expanduser` ignores `HOME` |
| all of `src/mimry` | ASCII-only output |

Next steps, in order:
1. Audit for anything that is now Windows-*specific* rather than
   Windows-*tolerant* (e.g. `tests/test_mimry_cli.py::_deny_read` uses `icacls`,
   guarded by `os.name != "nt"` — confirm the POSIX branch still works).
2. Try to actually run the suite on Linux locally — check for WSL or Docker on this
   machine. That is real evidence rather than waiting on CI.
3. `git push -u origin dev` and confirm all three OS lanes go green.
4. Only then merge.
