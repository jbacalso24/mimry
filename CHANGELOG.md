# Changelog

Notable user-facing changes are recorded here. MIMRY is pre-1.0; compatibility changes may still occur, but the supported release floor is tested before a release is tagged.

## 0.3.2 - 2026-10-13

### Upgrade notes

- From 0.3.1, upgrade the old way one last time: `uv tool upgrade mimry`, `pipx upgrade mimry` or `pip install --upgrade mimry`.
  On Windows, close the agents that use MIMRY first.
  From 0.3.2 on, `mimry upgrade` does it for you.

### New features

- `mimry upgrade` (or `mimry update`) upgrades MIMRY through whatever installed it: uv, pipx or pip ([#147](https://github.com/jbacalso24/mimry/pull/147)).
  On Windows it refuses while MIMRY servers are running, and `--stop-servers` stops them first.
  After upgrading it tells you how to switch each open agent to the new version.
- MIMRY installs as a Claude Code plugin: `/plugin marketplace add jbacalso24/mimry`, then `/plugin install mimry@mimry` ([#148](https://github.com/jbacalso24/mimry/pull/148)).
  It brings the skill, the hooks and the MCP server, and only needs `uv`.
- `npx skills add jbacalso24/mimry` installs the MIMRY skill for other agents ([#149](https://github.com/jbacalso24/mimry/pull/149)).
- The README lists the supported languages and the ones planned next ([#151](https://github.com/jbacalso24/mimry/pull/151)).

## 0.3.1 - 2026-10-13

### Upgrade notes

- The MCP server now lists 8 core tools by default ([#125](https://github.com/jbacalso24/mimry/pull/125)); set `MIMRY_MCP_TOOLS=all` to get every tool, or a comma-separated list.
- `mimry install --status` now exits 0, 1 or 2 ([#128](https://github.com/jbacalso24/mimry/pull/128)) and reports a skill written by an older MIMRY as out of date ([#140](https://github.com/jbacalso24/mimry/pull/140)).
  After upgrading, run `mimry install --platform <name>` again (with the same `--project`, `--always-on`, `--hooks` flags) to refresh the skill.
- On Windows, close the agents that use MIMRY before upgrading.
  Their running MCP servers lock MIMRY's files and the upgrade stops halfway.
  `uv tool install --force mimry` repairs a failed upgrade ([#139](https://github.com/jbacalso24/mimry/issues/139)).

### New features

- Agent hooks now refresh the index when a session starts and after each edit ([#127](https://github.com/jbacalso24/mimry/pull/127)).
- `find`, `symbol`, `related` and `semantic` reindex first when files changed, the same as `preflight`, in the CLI and MCP.
  When git reports the same working tree as the last index, the check is skipped, so an unchanged repository answers without walking the tree ([#98](https://github.com/jbacalso24/mimry/pull/98)).
- `mimry find` is scoped to the current directory inside an indexed repository.
  Use `--everywhere` or an explicit `--path-glob` to search outside that directory ([#92](https://github.com/jbacalso24/mimry/pull/92)).
- `mimry doctor` reports the install source, the running MCP servers and the agent registrations ([#126](https://github.com/jbacalso24/mimry/pull/126)).
- `mimry install` supports Cursor and GitHub Copilot in VS Code ([#122](https://github.com/jbacalso24/mimry/pull/122), [#124](https://github.com/jbacalso24/mimry/pull/124)).
- MIMRY records feedback from each commit automatically ([#119](https://github.com/jbacalso24/mimry/pull/119)).
- `mimry feedback stats` shows usage and ranking quality ([#121](https://github.com/jbacalso24/mimry/pull/121)).
- Each task now gets its own context pack ([#116](https://github.com/jbacalso24/mimry/pull/116)).
- Choose which files to index with a `mimry.toml`, and map file extensions to languages in it ([#104](https://github.com/jbacalso24/mimry/pull/104), [#109](https://github.com/jbacalso24/mimry/pull/109)).
- The search hook names `mimry find` on broad searches, so agents know to use it ([#106](https://github.com/jbacalso24/mimry/pull/106)).
- A new git worktree is set up on its first lookup, with its own index ([#101](https://github.com/jbacalso24/mimry/pull/101)).
- Results tell agents which lines of each match to read first ([#27](https://github.com/jbacalso24/mimry/pull/27)).
- Search can return short source snippets in the CLI and MCP, on request ([#23](https://github.com/jbacalso24/mimry/pull/23)).
- Search can filter by language and path, and pages of results stay stable across requests ([#24](https://github.com/jbacalso24/mimry/pull/24)).
- Add `mimry doctor`, which checks the installation, parsers and index and says how to fix what it finds, without changing anything ([#25](https://github.com/jbacalso24/mimry/pull/25)).
- `mimry preflight` shows elapsed time while it works ([#96](https://github.com/jbacalso24/mimry/pull/96)).
- When an agent connects to the MCP server, it is told when to use MIMRY, and the server reports its version ([#29](https://github.com/jbacalso24/mimry/pull/29)).

### Fixes

- The MCP server answers its first request in about 0.1 seconds, instead of 2 to 6 seconds while a large library loads at startup ([#113](https://github.com/jbacalso24/mimry/pull/113)).
- Keep MIMRY running on an incomplete install, and say how to repair it ([#19](https://github.com/jbacalso24/mimry/pull/19)).
- A skill written by an older MIMRY is now reported as out of date, not up to date ([#140](https://github.com/jbacalso24/mimry/pull/140)).
- Nested Git worktrees are excluded from their parent's index, so their files are not duplicated, and a folder named `worktrees` is no longer mistaken for a linked worktree ([#105](https://github.com/jbacalso24/mimry/pull/105), [#117](https://github.com/jbacalso24/mimry/pull/117)).
- Untracked files that `.gitignore` names are no longer indexed ([#30](https://github.com/jbacalso24/mimry/pull/30)).
- An index from another cache location is explained instead of crashing, and `preflight` rebuilds it ([#28](https://github.com/jbacalso24/mimry/pull/28)).
- An index from an older MIMRY is rebuilt automatically ([#88](https://github.com/jbacalso24/mimry/pull/88)).
- Feedback keeps the leading dot in file paths ([#120](https://github.com/jbacalso24/mimry/pull/120)).
- Every graph match gets a content score before results are cut to the limit ([#26](https://github.com/jbacalso24/mimry/pull/26)).
- Installing the skill on Windows waits out file locks instead of failing ([#81](https://github.com/jbacalso24/mimry/pull/81)).
- The global skill installs under the directory named by `CODEX_HOME` ([#76](https://github.com/jbacalso24/mimry/pull/76)).
- Installs use the MCP library version that CI tests, and the next major version cannot arrive untested ([#77](https://github.com/jbacalso24/mimry/pull/77)).

### Documentation

- The README is reworked around what MIMRY does for coding agents, and says that each agent starts its own MCP server ([#97](https://github.com/jbacalso24/mimry/pull/97), [#86](https://github.com/jbacalso24/mimry/pull/86)).
- The README shows agent support status, and each agent's setup steps are on a docs page ([#108](https://github.com/jbacalso24/mimry/pull/108), [#91](https://github.com/jbacalso24/mimry/pull/91)).
- The project website has searchable setup guides ([#22](https://github.com/jbacalso24/mimry/pull/22)).
- Published token benchmark results include a holdout2 run of main, and holdout3 results for whole-file and line-range reads ([#100](https://github.com/jbacalso24/mimry/pull/100), [#94](https://github.com/jbacalso24/mimry/pull/94)).
- The README welcomes contributions, explains how to open a pull request, and says MIMRY is free to use ([#20](https://github.com/jbacalso24/mimry/pull/20)).

## 0.3.0 - 2026-10-07

### New features

- Stale indexes are refreshed before a task runs ([#18](https://github.com/jbacalso24/mimry/pull/18)).
- Markdown headings and JavaScript modules are indexed ([#18](https://github.com/jbacalso24/mimry/pull/18)).
- Code is found by what is inside files, and agents get a shorter context pack ([#15](https://github.com/jbacalso24/mimry/pull/15)).
- PDF and SVG files are indexed, and six more languages are supported ([#14](https://github.com/jbacalso24/mimry/pull/14)).
- `mimry export` writes the graph as an interactive HTML page, GraphML, a Cypher script or a notes vault ([#14](https://github.com/jbacalso24/mimry/pull/14)).
- Git hooks refresh the index in the background after commits, merges, checkouts and rewrites, and `mimry git-hooks` manages them ([#14](https://github.com/jbacalso24/mimry/pull/14)).
- `mimry install` registers the MCP server with the agent at user scope, so one registration serves every project ([#16](https://github.com/jbacalso24/mimry/pull/16)).

### Fixes

- Installing no longer hangs on Windows ([#12](https://github.com/jbacalso24/mimry/pull/12)).
- Content search reads each file's whole text, not only its first 40 lines ([#15](https://github.com/jbacalso24/mimry/pull/15)).
- Source files are no longer refused because one value only looks like a credential ([#16](https://github.com/jbacalso24/mimry/pull/16)), and source that only names a credential is kept ([#11](https://github.com/jbacalso24/mimry/pull/11)).
- Source files rank above tests ([#11](https://github.com/jbacalso24/mimry/pull/11)).

### Documentation

- MIMRY has a security policy and the full MIT license text ([#9](https://github.com/jbacalso24/mimry/pull/9)).

## 0.2.1 - 2026-10-02

### Documentation

- MIMRY has a project website and a listing in the MCP Registry ([#6](https://github.com/jbacalso24/mimry/pull/6)).
- The README is readable by AI agents, and the project adds an llms.txt file ([#5](https://github.com/jbacalso24/mimry/pull/5)).

## 0.2.0 - 2026-09-29

### New features

- Indexing is much faster: a full index of a large sample repository dropped from about 100 seconds to 16 seconds.
  An unchanged reindex takes about 2.7 seconds ([#4](https://github.com/jbacalso24/mimry/pull/4)).
- Every command shares one readable output style, with a headline, indented details and aligned tables ([#4](https://github.com/jbacalso24/mimry/pull/4)).

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
