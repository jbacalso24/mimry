# Changelog

Notable user-facing changes are recorded here. MIMRY is pre-1.0; compatibility changes may still occur, but the supported release floor is tested before a release is tagged.

## Unreleased

### Determinism

- Add the narrow `mimry plan` recursive decomposer: deterministic leaf-only splits, canonical repo-local JSON storage, terminal/JSON/Markdown projections, structural validation, semantic SHA-256, deterministic inventory, and read-only MCP parity. It stores explicit tree structure only and does not execute, schedule, refine, or track work.
- Establish an explicit determinism contract splitting canonical semantic state (file/symbol/edge/community/chunk identities, parser facts, ranking and tie-breaking, context-pack evidence ordering) from the operational envelope (timestamps, generation UUIDs, absolute paths, mtimes, feedback event IDs, SQLite byte layout). Documented in `docs/determinism.md`.
- Derive canonical IDs from the schema version and the repository-relative path instead of the absolute checkout path. Two clones of the same repository at different locations now produce identical file, symbol, edge, community, and semantic-chunk identities. Semantic `chunk_id` no longer carries the machine-local root UUID either.
- Refuse index generations built under the old path-dependent identity scheme (`GENERATION_SCHEMA_VERSION` 1-3) with an actionable rebuild message, rather than partially reusing identities nothing else in the build produces.
- Sort filesystem traversal and every persisted collection by a single normalization rule (`canonical_rel_path`: POSIX separators, NFC, case preserved, codepoint-ordered and therefore locale-independent), and reject same-root native paths that collide after NFC normalization before publication.
- Reject duplicate node, symbol, and edge IDs whose records conflict, instead of resolving them by last write. Records identical in every field are deduplicated under a documented policy.
- Give edges a total order over every canonical field and a documented EXTRACTED-over-INFERRED conflict rule, applied at every graph assembly site rather than only inside `build_graph`.
- Give symbol resolution a total, documented selection order (kind priority, relative path, line start, line end, symbol ID). Genuinely ambiguous inheritance and import targets are still declined rather than resolved by picking the first candidate.
- Add deterministic secondary ordering before every SQL cutoff. A tied BM25 rank no longer lets physical row order decide which rows survive `LIMIT`, and semantic chunk reads are ordered so explanation previews and reason labels are stable.
- Acquire each file through bounded matching reads, derive its hash, size, mtime, and parser input from that snapshot, then verify post-acquisition freshness after adapters finish. A file changed mid-index is marked unindexable; mixed-version evidence is never persisted.
- Add `mimry digest`, which hashes normalized semantic state rather than raw SQLite bytes or the generation manifest.
- Add a real end-to-end determinism gate over a frozen multi-language fixture (`scripts/determinism_matrix.py`) covering scanner, Python/TS/Java/PHP/Go/SQL/Markdown parsing, graph and communities, SQLite/FTS, local semantic retrieval, and context-pack generation, across creation order, checkout root, `PYTHONHASHSEED`, clean-cache repeats, and locale/timezone. CI runs it on Linux, macOS, and Windows and a dedicated job compares every platform's digest against a committed golden value.
- Convert the `__main__` self-checks in the graph core into committed pytest tests.

### Graph engine

- Replace the Graphify dependency with MIMRY's own graph engine in `src/mimry/core/`. Indexing parses each file once and derives `defines`, `imports`, and `calls` edges from what it already read, instead of copying the tree to a temporary directory and re-parsing it in a subprocess.
- Resolve import statements and call sites to real files and symbols. MIMRY previously collected import strings but never resolved them, so its graph had no file-to-file edges at all. Unresolvable or ambiguous references are dropped rather than guessed.
- Extend language coverage to Go, Rust, and C# alongside Python, JavaScript, TypeScript, JSX, and TSX, at no new dependency cost.
- Rank by following relationships, not only by matching text: query relevance now propagates two hops along edges.
- Stop demoting full-text evidence whenever the graph has an opinion. A content-only result was previously multiplied by 0.75 if the graph returned anything, and a file found by both signals had its content score discarded; corroborating evidence now adds.
- Measured against the recorded baselines on nine cases: ndcg@5 0.6300 -> 0.6907, recall@5 0.8542 -> 0.8958, primary-hit@3 0.5000 -> 0.7500, decoy rate 0.1351 -> 0.1081, and time to a queryable indexed graph roughly halved.
- Rename public graph terminology for the native engine: `--skip-graph`, `mimry status`, the MCP `skip_graph` argument, and the status payload key `graph` are canonical. A compatibility window keeps `--skip-graphify`, `mimry graphify status`, MCP `skip_graphify`, and the `graphify` status key functional for existing clients. The graph engine identifier is `mimry-core`.

### Cross-platform

- Fix Windows portability defects in home-directory sandboxing (including `APPDATA`, `LOCALAPPDATA`, and `XDG_DATA_HOME` isolation), descriptor syncing, timestamp identity checks, capability-guarded file operations, and cp1252-safe CLI output. UTF-8 artifacts preserve original Unicode.
- Test Windows, Linux, and macOS across Python 3.11-3.13 in the committed CI matrix.

### Packaging and support floor

- Restrict source distributions to reviewed source, tests, documentation, and license files; exclude vendored, generated, cache, build, and local-work bulk.
- Add artifact contract tests and Windows/Linux/macOS CI for Python 3.11, 3.12, and 3.13; the unlocked sdist lane invokes only extracted artifact source/scripts.
- Package `determinism_aggregate.py`, support standards-valid XLSX inline strings, reject empty explicit edge IDs, and fail closed on unknown future generation schemas while preserving the old-schema upgrade classification.
- Pin the proven `tree-sitter-language-pack` 1.12.2 compatibility floor after 1.13.5 broke TSX symbol extraction, and test unlocked extracted artifacts in CI.
- Define 0.1.x as an internal checkout/direct-wheel release rather than claiming compatibility with PyPI-style indexes.
