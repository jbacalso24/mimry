# How MIMRY works

This page covers what MIMRY builds and how it keeps that up to date.
For the commands themselves, see [commands.md](commands.md).

## What it builds

```text
project-root/
├─ .mimry/                  # local settings, gitignored
│  └─ mimry-out/            # generated output for agents and humans
│     ├─ context/<task>-<hash>.md  # context pack per task
│     ├─ context/latest.md  # copy of the newest pack
│     ├─ graph/             # relationship graph artifacts
│     └─ export/            # graph exports (optional)
│        ├─ graph.html      # interactive viewer
│        ├─ graph.graphml   # Gephi/yEd format
│        ├─ graph.cypher    # Neo4j import script
│        └─ obsidian/       # linked notes vault
└─ source files...
```

The index itself lives in your user cache directory, outside the repo.
It is a generated artifact, not a source of truth.

A context pack contains the query and index freshness, ranked files with reasons and the line ranges to read first, symbol hints, relationships, a suggested reading order, likely edit surfaces versus support files, risk notes for generated or sensitive paths, verification commands from the project's manifests, Makefile and justfile targets, tox and nox sessions, and CI workflow steps, and a final-report checklist.
It uses relative paths and never includes full source or secret values.
See [`context-packs.md`](context-packs.md) for the contract.

### Graph exports

Export the graph with `mimry export --format {html,graphml,cypher,obsidian}` to use it in external tools or view it interactively.

- **html**: self-contained interactive viewer with canvas-based force-directed layout, search, and node inspection.
- **graphml**: import into Gephi, yEd, or networkx for further analysis.
- **cypher**: Neo4j import script for `cypher-shell -f`, with batched UNWIND and MERGE statements and no plugins required.
- **obsidian**: vault of markdown notes mirroring the repo structure, with wikilinks for dependencies. Re-exporting replaces a vault MIMRY created; `--force` writes into another non-empty directory without deleting anything.

All formats are deterministic and stay inside the chosen output directory.

## Graph engine

MIMRY builds its own relationship graph in `src/mimry/core/`.
Indexing parses each file once and derives these edges from what it read:

| relation | shape | from |
|---|---|---|
| `defines` | file to symbol | every parsed definition |
| `imports` | file to file | resolved import/using statements |
| `calls` | symbol to symbol | call sites resolved to a definition |
| `inherits` | symbol to symbol | base classes and implemented interfaces |
| `references` | file to file, file to symbol | markdown links and SQL table mentions |

39 languages parsed: Python, JavaScript, TypeScript, Go, Rust, C#, Java, PHP, C, C++, Ruby, Kotlin, Scala, Swift, Bash, PowerShell, Lua, Perl, Dart, Objective-C, Zig, Elixir, Haskell, OCaml, R, Julia, Solidity, Fortran, Pascal, CSS, SCSS, HTML, Vue, Svelte, JSON, YAML, TOML, XML and Terraform (HCL).
Markdown, text, `.docx`, `.xlsx`, `.pdf`, and `.svg` are indexed for content and can carry `references` edges.
Documents up to 20 MB are read (PDFs up to 200 pages); other files stop at 1 MB.
Raster images (PNG, JPG) require OCR or vision models and are out of scope.

Resolution never guesses.
A target that is ambiguous, or defined outside the repo, produces no edge rather than a plausible one.
`inherits` additionally resolves a base type by name across the repo when exactly one file defines that name, because C# reaches base types through `using <namespace>` rather than a path import.
Nodes are grouped into communities by deterministic label propagation, and `graph.json` is byte-identical across rebuilds of unchanged sources.
Adding a language is a table entry in `core/languages.py`; the pinned `tree-sitter-language-pack` fetches each grammar the first time it is needed and caches it.

Artifacts land in `.mimry/mimry-out/graph/` (`graph.json`, `GRAPH_REPORT.md`, `manifest.json`) during `mimry index`; there is no separate build step.
`GRAPH_REPORT.md` ranks the most important nodes and edges and includes a "Suggested Questions" section with prompts paired to `mimry` commands.
Use `mimry report` to print the current graph report.

## Freshness and incremental indexing

Like git, MIMRY treats a file as unchanged while its size, inode, and nanosecond mtime still match the snapshot it was indexed from, so status checks and reindexes do not re-read unchanged files.
A reindex re-parses only new and changed files, and its output is identical to a full rebuild.
Files modified within two seconds of the previous index scan are always re-read, which covers edits that land inside one timestamp tick.
The one edit this cannot see is a rewrite that deliberately keeps size, inode, and mtime.
Run `mimry status --verify` to re-hash every file, and `mimry index --full` to re-parse every file.

### Keep the index fresh automatically

Three opt-in ways refresh the index for you; none runs by default.

- `mimry install --project --platform claude-code --hooks` (or `--platform codex`) adds agent hooks that answer a search for a known symbol with its definition and callers, suggest `mimry find` for other broad searches, and refresh the index in the background when a session starts and after each file edit.
  The agent's next query does not wait for that refresh.
- `mimry watch [--interval SECONDS]` polls in the foreground (every 2 seconds by default) and refreshes once a burst of edits settles.
- `mimry git-hooks install` adds post-commit, post-merge, post-checkout, and post-rewrite hooks that refresh in the background, so `git commit` returns immediately.
  The post-commit hook first records feedback from the commit, then refreshes.
  A commit that touches none of the files the last pack suggested counts against that pack only when its message or a changed file's name shares a word with the pack's task.
  Otherwise nothing is recorded, so an unrelated typo fix does not log misses.
  Running it again is safe, `mimry git-hooks status` shows what is installed, and `mimry git-hooks uninstall` removes only MIMRY's lines.

If a hook manager such as husky owns your hooks, add `mimry refresh` to its post-commit hook instead.

## Feedback learning

`mimry feedback` records which files an agent actually used after a task.
It is stored in the repo's local cache, is not telemetry, and becomes an explainable ranking signal for similar future queries.

```bash
mimry feedback --query "fix board card click" \
  --context <context pack path> \
  --opened src/features/boards/api.ts \
  --changed src/app/api/bridge/route.ts \
  --missed bridge/fastapi_app.py \
  --ignored README.md \
  --verification "npm run build passed" \
  --outcome passed
```

Feedback is added to the source ranking, so it can outrank a weaker exact filename, symbol or graph match.
A file can gain up to half the top score of the query, scaled by how closely the past task matched.

Each signal counts like this:

- `--changed` and `--missed` count fully, whatever the outcome, because they are facts about the code.
- `--opened` counts 0.4 for a passed task, 0.24 for partial or unknown, and 0.12 for failed or blocked.
- `--ignored` counts -0.3, and lowers a file by at most a quarter of the top score, never below 1.
- A file counts once per similar task, with its largest value, and each task counts by how closely its query matches yours, squared, so a loose match counts far less than a near-identical task.
- A suggestion that three similar tasks left unused, while those tasks changed or opened other files, drops 20%, unless a similar task changed, opened or missed it.

`--outcome` says whether the task succeeded.
Changed and missed files count fully whatever the outcome, and the outcome only weights files that were opened.

## Structured adapters

Adapters teach MIMRY about common project surfaces: `python-ast`, `typescript-ast`, `config-manifest`, `nextjs-app-router`, `fastapi`, `react-native-expo`, `sql-schema`, `markdown-docs`, and `generic-text`.
Run `mimry adapters --verbose` to see what each extracts.

## Cache generation and lock safety

Index publication, feedback writes, readers, generation cleanup, and `mimry cache wipe --current` share one per-root operation lock.
POSIX readers share it; Windows readers take it exclusively because `msvcrt.locking` has no shared mode.
Lock waits are bounded to 10 seconds by default; set `MIMRY_LOCK_TIMEOUT_SECONDS` to raise the bound.
Timeout errors include the lock path and holder metadata, so a stuck process can be diagnosed without deleting lock files blindly.

Published indexes are immutable generations.
Startup and successful publication remove abandoned staging trees and keep only the current and last-known-good generations.
Generation manifests checksum immutable sidecars, and SQLite semantic rows carry the same generation identity plus a content checksum.
`mimry cache wipe --all` is disabled until MIMRY has a proven cache-global writer protocol; use `--current` instead.

## Good roots vs bad roots

MIMRY works best when one root is one meaningful working context: a repo or a project folder.

Good roots: `~/Documents/project-a`, `C:\Users\you\Documents\project-a`, `D:\Work\active-product`.

Bad roots: `/`, `~`, `~/.config`, `~/.cache`, `C:\`, `C:\Users\you`, `AppData`, `node_modules`.

Whole-drive or whole-home indexing is noisy, slow, and likely to include secrets, caches, and unrelated projects.
For a personal "global" memory, make a curated folder such as `~/Workspace` and put only useful projects and docs in it.

## Platform details

Paths are handled POSIX-style internally, and platform-specific system calls are capability-guarded rather than assumed.
Handled explicitly, because each was a real bug:

- `Path.home()` resolves from `USERPROFILE` on Windows, not `HOME`.
- `fsync` needs a writable descriptor on Windows, and `st_ctime` there is creation time.
- `os.utime(follow_symlinks=False)` and byte-range lock semantics are capability-guarded.
- Human CLI output degrades Unicode safely on strict cp1252 consoles, while JSON, Markdown, and index artifacts stay UTF-8.
- Subprocesses never inherit stdin, which under the MCP stdio server is the JSON-RPC channel.
