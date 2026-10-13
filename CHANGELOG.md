# Changelog

Notable user-facing changes are recorded here. MIMRY is pre-1.0; compatibility changes may still occur, but the supported release floor is tested before a release is tagged.

## 0.3.1 - 2026-10-13

### Before you upgrade

- The MCP server now shows 8 core tools by default ([#125](https://github.com/jbacalso24/mimry/pull/125)).
  Set `MIMRY_MCP_TOOLS=all` if you want all of them back.
- After upgrading, run `mimry install --platform <name>` again with your usual flags to refresh the skill.
  `mimry install --status` now tells you when it's out of date ([#128](https://github.com/jbacalso24/mimry/pull/128), [#140](https://github.com/jbacalso24/mimry/pull/140)).
- Windows: close any agent using MIMRY before you upgrade, or the upgrade can fail halfway.
  If that happens, `uv tool install --force mimry` fixes it ([#139](https://github.com/jbacalso24/mimry/issues/139)).

### New

- Install into Cursor and GitHub Copilot in VS Code ([#122](https://github.com/jbacalso24/mimry/pull/122), [#124](https://github.com/jbacalso24/mimry/pull/124)).
- `mimry doctor` checks your install, parsers and index, and tells you how to fix problems ([#25](https://github.com/jbacalso24/mimry/pull/25), [#126](https://github.com/jbacalso24/mimry/pull/126)).
- `--hooks` now also refreshes the index in the background when a session starts and after each edit ([#127](https://github.com/jbacalso24/mimry/pull/127)).
- `find`, `symbol`, `related` and `semantic` reindex changed files first, like `preflight` ([#98](https://github.com/jbacalso24/mimry/pull/98)).
- Results show which lines to read first ([#27](https://github.com/jbacalso24/mimry/pull/27)).
- `mimry find` searches the folder you're in; use `--everywhere` for the whole repo ([#92](https://github.com/jbacalso24/mimry/pull/92)).
- Filter search by language and path, and ask for short source snippets ([#23](https://github.com/jbacalso24/mimry/pull/23), [#24](https://github.com/jbacalso24/mimry/pull/24)).
- `mimry.toml` picks which files get indexed and maps extensions to languages ([#104](https://github.com/jbacalso24/mimry/pull/104), [#109](https://github.com/jbacalso24/mimry/pull/109)).
- Each task gets its own context pack ([#116](https://github.com/jbacalso24/mimry/pull/116)).
- New git worktrees get set up on their first lookup ([#101](https://github.com/jbacalso24/mimry/pull/101)).
- Commits record feedback automatically, and `mimry feedback stats` shows how it's going ([#119](https://github.com/jbacalso24/mimry/pull/119), [#121](https://github.com/jbacalso24/mimry/pull/121)).
- The search hook suggests `mimry find` on broad searches ([#106](https://github.com/jbacalso24/mimry/pull/106)).
- `preflight` shows a timer while it works ([#96](https://github.com/jbacalso24/mimry/pull/96)).
- The MCP server tells agents when to use MIMRY and reports its version ([#29](https://github.com/jbacalso24/mimry/pull/29)).

### Fixed

- The MCP server starts in about 0.1 s instead of 2-6 s ([#113](https://github.com/jbacalso24/mimry/pull/113)).
- `install --status` said an old skill was up to date ([#140](https://github.com/jbacalso24/mimry/pull/140)).
- A broken install crashed instead of saying how to repair it ([#19](https://github.com/jbacalso24/mimry/pull/19)).
- An index from an older MIMRY or another cache location caused errors; it's now rebuilt ([#28](https://github.com/jbacalso24/mimry/pull/28), [#88](https://github.com/jbacalso24/mimry/pull/88)).
- Nested worktrees got indexed twice, and a folder named `worktrees` was mistaken for one ([#105](https://github.com/jbacalso24/mimry/pull/105), [#117](https://github.com/jbacalso24/mimry/pull/117)).
- Untracked files listed in `.gitignore` were indexed ([#30](https://github.com/jbacalso24/mimry/pull/30)).
- Some graph matches were cut before their content was scored ([#26](https://github.com/jbacalso24/mimry/pull/26)).
- Feedback dropped the leading dot from paths like `.github/` ([#120](https://github.com/jbacalso24/mimry/pull/120)).
- Installing the skill on Windows failed when a file was briefly locked ([#81](https://github.com/jbacalso24/mimry/pull/81)).
- The global Codex skill ignored `CODEX_HOME` ([#76](https://github.com/jbacalso24/mimry/pull/76)).
- The MCP library version is now pinned to the range CI tests ([#77](https://github.com/jbacalso24/mimry/pull/77)).

### Docs

- Rewrote the README around what MIMRY does for agents, with agent support status and per-agent setup steps ([#97](https://github.com/jbacalso24/mimry/pull/97), [#108](https://github.com/jbacalso24/mimry/pull/108), [#91](https://github.com/jbacalso24/mimry/pull/91), [#86](https://github.com/jbacalso24/mimry/pull/86)).
- Setup guides on the website ([#22](https://github.com/jbacalso24/mimry/pull/22)).
- New token benchmark results for holdout2 and holdout3 ([#100](https://github.com/jbacalso24/mimry/pull/100), [#94](https://github.com/jbacalso24/mimry/pull/94)).
- How to contribute ([#20](https://github.com/jbacalso24/mimry/pull/20)).

## 0.3.0 - 2026-10-07

### New

- Search looks inside files, and context packs are shorter ([#15](https://github.com/jbacalso24/mimry/pull/15)).
- Index PDFs, SVGs, Markdown headings, JavaScript modules and six more languages ([#14](https://github.com/jbacalso24/mimry/pull/14), [#18](https://github.com/jbacalso24/mimry/pull/18)).
- Stale indexes are refreshed before a task ([#18](https://github.com/jbacalso24/mimry/pull/18)).
- `mimry export` writes the graph as HTML, GraphML, Cypher or a notes vault ([#14](https://github.com/jbacalso24/mimry/pull/14)).
- `mimry git-hooks` refreshes the index after commits, merges, checkouts and rebases ([#14](https://github.com/jbacalso24/mimry/pull/14)).
- `mimry install` registers the MCP server once for all your projects ([#16](https://github.com/jbacalso24/mimry/pull/16)).

### Fixed

- `mimry install` hung on Windows ([#12](https://github.com/jbacalso24/mimry/pull/12)).
- Content search only read the first 40 lines of each file ([#15](https://github.com/jbacalso24/mimry/pull/15)).
- Source files that only mentioned a credential got skipped ([#11](https://github.com/jbacalso24/mimry/pull/11), [#16](https://github.com/jbacalso24/mimry/pull/16)).
- Tests ranked above the source they test ([#11](https://github.com/jbacalso24/mimry/pull/11)).

### Docs

- Security policy and full MIT license text ([#9](https://github.com/jbacalso24/mimry/pull/9)).

## 0.2.1 - 2026-10-02

### Docs

- Project website and MCP Registry listing ([#6](https://github.com/jbacalso24/mimry/pull/6)).
- An agent-friendly README and `llms.txt` ([#5](https://github.com/jbacalso24/mimry/pull/5)).

## 0.2.0 - 2026-09-29

### New

- Much faster indexing: a full index of a large repo went from about 100 s to 16 s, and an unchanged reindex takes about 2.7 s ([#4](https://github.com/jbacalso24/mimry/pull/4)).
- Cleaner, consistent output across all commands ([#4](https://github.com/jbacalso24/mimry/pull/4)).

## 0.1.0 - 2026-09-07

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

- Replace the external graph dependency with MIMRY's own graph engine in `src/mimry/core/`. Indexing parses each file once and derives `defines`, `imports`, and `calls` edges from what it already read, instead of copying the tree to a temporary directory and re-parsing it in a subprocess.
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
