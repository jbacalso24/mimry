# Changelog

Notable user-facing changes are recorded here. MIMRY is pre-1.0; compatibility changes may still occur, but the supported release floor is tested before a release is tagged.

## Unreleased

### Graph engine

- Replace the Graphify dependency with MIMRY's own graph engine in `src/mimry/core/`. Indexing parses each file once and derives `defines`, `imports`, and `calls` edges from what it already read, instead of copying the tree to a temporary directory and re-parsing it in a subprocess.
- Resolve import statements and call sites to real files and symbols. MIMRY previously collected import strings but never resolved them, so its graph had no file-to-file edges at all. Unresolvable or ambiguous references are dropped rather than guessed.
- Extend language coverage to Go, Rust, and C# alongside Python, JavaScript, TypeScript, JSX, and TSX, at no new dependency cost.
- Rank by following relationships, not only by matching text: query relevance now propagates two hops along edges.
- Stop demoting full-text evidence whenever the graph has an opinion. A content-only result was previously multiplied by 0.75 if the graph returned anything, and a file found by both signals had its content score discarded; corroborating evidence now adds.
- Measured against the recorded baselines on nine cases: ndcg@5 0.6300 -> 0.6907, recall@5 0.8542 -> 0.8958, primary-hit@3 0.5000 -> 0.7500, decoy rate 0.1351 -> 0.1081, and time to a queryable indexed graph roughly halved.
- **Breaking:** the `mimry graphify` command is removed, `--skip-graphify` becomes `--skip-graph`, and the MCP status payload key `graphify` becomes `graph`. The graph engine identifier is now `mimry-core`.

### Cross-platform

- Fix six Windows defects that made the benchmark unrunnable and left 75 of 183 tests failing: eager `Path.home()` evaluation in `cache_home`, a sandbox that set `HOME` but not `USERPROFILE`, `fsync` on a read-only descriptor, `os.utime(follow_symlinks=False)` where unsupported, `st_ctime_ns` compared between `os.fstat` and `Path.lstat` (Windows reports creation time at different precision from each, so every file read as tampered and `status`/`path`/`why` were permanently stale), and non-ASCII characters in CLI output that crash a cp1252 console.
- State supported platforms in the README, including that Windows is not yet in the CI matrix.

### Packaging and support floor

- Restrict source distributions to reviewed source, tests, documentation, and license files; exclude vendored, generated, cache, build, and local-work bulk.
- Add artifact contract tests and Linux/macOS CI for Python 3.11, 3.12, and 3.13.
- Pin the proven `tree-sitter-language-pack` 1.12.2 compatibility floor after 1.13.5 broke TSX symbol extraction, and test unlocked extracted artifacts in CI.
- Define 0.1.x as an internal checkout/direct-wheel release rather than claiming compatibility with PyPI-style indexes.
