# MIMRY core engine — handoff state

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
Languages: Python, JS, JSX, TS, TSX, Go, Rust, C#, Java, PHP.

Every node carries a `line` (int for symbols, `None` for files), so graph answers
can point at `path:line` instead of just a path.

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
   Rule 2 in `resolve.py` is Python-only and gated on the importer ending `.py`.
   Known miss left in place: a src-layout Python import (`mimry.core.build` where
   the file is `src/mimry/core/build.py`) resolves to nothing, because rule 2
   probes only `mimry/core/build.py`. Letting it fall through to rule 4's suffix
   match does resolve it, and the edge is correct — but the extra graph evidence
   changes which reason four CLI/MCP tests report. Decide that one deliberately.
6. **All source is ASCII** — `tests/test_encoding.py` enforces it and a cp1252 Windows
   console raises on non-ASCII output.
7. **Use `posixpath`, never `os.path`,** for `rel_path` joins. `os.path.normpath`
   returns backslashes on Windows and would resolve nothing there while passing on Linux.

## Current status

- Do not carry forward fixed test counts: report the exact `pytest` result from the
  commit being handed off.
- CI is committed (`.github/workflows/ci.yml`): 3 OS x Python 3.11/3.12/3.13, plus
  `determinism-aggregate` (requires evidence from all 9 cells and compares canonical
  digest, retrieval rankings, and context pack against committed golden values) and
  `benchmark-gate` (requires the frozen usefulness floors to pass). Cite the actual run
  URL and its conclusion; a green run is required but does not replace review.
- The frozen native-graph benchmark is enforced by the required `benchmark-gate` job and
  by `tests/test_benchmark_quality_gate.py`, which also asserts the committed thresholds
  still equal `DEFAULT_THRESHOLDS`. Never weaken a floor to get green, and never turn the
  historical Graphify baseline into a current PASS claim -- report the real numbers.
- Run `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pytest -q`
  before handoff, then include their exact outputs.

## Linux and macOS status

**Partially verified on real Linux** (WSL Ubuntu 24.04.4, Python 3.12.3), no
installation required because these modules are pure stdlib:

- `core/build.py`, `core/cluster.py`, `core/resolve.py`, `core/report.py`,
  `core/documents.py` self-checks all print `OK` on Linux.
- `scripts/cross_platform_check.py` builds a graph from fixed in-memory input and
  prints a digest. Windows prints
  `425a722173ccb7c7eeabe0189feabec7f88c1a484891e622d8224cc246a897af`, and it is
  stable across `PYTHONHASHSEED` values. (It was
  `76f2e5c39d5ab85a39c48036dbadcfd6220b0ea0c42684137c8947ba8688b8c9` before nodes
  gained `line` and the report gained surprise scores; **Linux needs re-running to
  confirm it still agrees.**) That means the engine computes
  byte-identically across platforms, so a cached index is portable between machines.

**Still unverified:** the full suite on Linux/macOS (CLI, MCP, state recovery,
privacy hardening). Those need `tree-sitter` and `fastmcp` installed. WSL here has
no `pip`/`ensurepip` and no passwordless sudo, so local install needs either
`sudo apt install python3-venv python3-pip` or fetching `uv` — both need the
operator.

To repeat the Linux check:

```
wsl -d Ubuntu -e bash -lc "cd ~/mimry-linux && python3 scripts/cross_platform_check.py"
```

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

For the latest verification state, use the commit's test output or release record;
this handoff intentionally does not freeze ephemeral branch, machine, or test-count
claims.
