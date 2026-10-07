<p align="center">
  <img src="https://raw.githubusercontent.com/jbacalso24/mimry/main/assets/banner.png" alt="MIMRY - Local repo memory for coding agents" width="100%">
</p>

<h1 align="center">MIMRY</h1>

<!-- mcp-name: io.github.jbacalso24/mimry -->

<p align="center">
  <strong>Local repo memory for coding agents.</strong>
  <br />
  Ranked files, context packs, a code relationship graph, and feedback-tuned search, built from your own codebase.
</p>

<p align="center">
  <a href="https://pypi.org/project/mimry/"><img alt="PyPI" src="https://img.shields.io/pypi/v/mimry"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-blue">
  <img alt="License MIT" src="https://img.shields.io/badge/license-MIT-green">
  <img alt="Local first" src="https://img.shields.io/badge/local--first-yes-purple">
  <img alt="CLI" src="https://img.shields.io/badge/interface-CLI-black">
  <img alt="MCP" src="https://img.shields.io/badge/agent-MCP-orange">
</p>

<p align="center">
  <a href="https://jbacalso24.github.io/mimry/"><strong>Website</strong></a>
  ·
  <a href="#install"><strong>Install</strong></a>
  ·
  <a href="#quickstart"><strong>Quickstart</strong></a>
  ·
  <a href="#for-ai-agents"><strong>For AI agents</strong></a>
  ·
  <a href="#connect-mimry-to-your-agent"><strong>MCP setup</strong></a>
  ·
  <a href="#commands"><strong>Commands</strong></a>
  ·
  <a href="#development"><strong>Development</strong></a>
</p>

---

## What is MIMRY?

MIMRY is a local command-line tool and MCP server that gives AI coding agents a memory of a code repository.
It indexes a repo once, keeps the index fresh incrementally, and answers "which files matter for this task?" with a ranked, evidence-backed list and a written context pack.

- **Who it is for:** anyone using an AI coding agent (Claude Code, Codex, Copilot CLI, Gemini CLI, Cursor-style tools, and others) on a real codebase.
- **The problem it solves:** agents start every task cold. They grep blindly, reread the same files, miss the file that actually matters, and burn tokens and time doing it.
- **What it does instead:** one command, `mimry preflight "<task>"`, returns the files to read first, how they connect, what to verify with, and a context pack the agent reads before touching code.
- **How it works:** it parses source files into symbols and a relationship graph (imports, calls, definitions, inheritance, references), combines that with full-text and local semantic search, and learns from feedback about which files agents actually used.
- **Where it runs:** entirely on your machine.
  No code is uploaded, there are no remote embeddings, and there is no telemetry.
- **What it is not:** it is not a source of truth, a code generator, or an LLM.
  It narrows where to look; source files, tests, and build output remain the final truth.

## Install

MIMRY is a Python package on [PyPI](https://pypi.org/project/mimry/) and needs Python 3.11 or newer.
It works on Windows, Linux, and macOS.

```bash
uv tool install mimry
```

`pipx install mimry` and `pip install mimry` also work.
The install provides two commands: `mimry` (the CLI) and `mimry-mcp` (the MCP server for agents).

Check it:

```bash
mimry --help
```

Upgrade later with `uv tool upgrade mimry`.
It updates the install in place and adds any new dependencies.
On Windows, close agent sessions that use MIMRY first: each one keeps `mimry-mcp` running, and Windows will not replace files a running program has open.
If `mimry status` reports that the install is incomplete, it prints the command that repairs it, usually `uv tool upgrade mimry`.

## Quickstart

Run these inside the project you want an agent to work on:

```bash
cd /path/to/your/project
mimry init
mimry index
mimry preflight "fix the login redirect bug"
```

`preflight` answers like this:

```text
✓ Context ready for "fix the login redirect bug"
  Index is up to date (3,105 files, indexed just now)
  Context pack: .mimry/mimry-out/context/latest.md

Start with
   1  src/auth/session.py
      name matches · path matches
   2  src/app/login/page.tsx
      name matches
   3  tests/test_auth.py
      path matches

Next: read .mimry/mimry-out/context/latest.md before opening files.
```

`preflight` also sets MIMRY up and builds the index on first use, so in practice it is the only command an agent needs to start a task.

## For AI agents

This section is written for an AI coding agent working in a repository where MIMRY is installed.

### When to use MIMRY

Start every task with MIMRY.
It replaces exploring the repository by grep: guessing search terms, then reading files to see whether they matter.
Run it whenever you need to find where something lives, what a task touches, or how two parts of the code connect.
For an exact string you already know, such as an identifier or an error message, use `rg` directly, or `git grep` when `rg` is not installed.

### The workflow

1. **Start the task:** `mimry preflight "<task in plain words>"`.
2. **Read the context pack:** `.mimry/mimry-out/context/latest.md`.
   It lists ranked files with reasons, key symbols, relationships, risky paths, and verification commands.
3. **Navigate with focused queries** instead of scanning the tree:
   `mimry find "<query>"`, `mimry symbol <name>`, `mimry related "<query>"`, `mimry explain "<task>"`, `mimry path <a> <b>`, `mimry why <file> --query "<task>"`.
4. **Verify against the source.**
   MIMRY output is navigation, not proof.
   Open the files, run the tests, and trust the build output over any ranking.
5. **Record what helped:**
   `mimry feedback --query "<task>" --opened <files> --changed <files> --verification "<command and result>" --outcome passed`.
   Future searches for similar tasks rank those files higher.

### Rules

- `preflight`, `context`, `brief` and `route` reindex first when files changed or MIMRY was upgraded, so a task never starts from a stale index.
- If a quick lookup such as `find` or `symbol` says the index is out of date, run `mimry reindex` (fast: it only re-reads changed files).
- Treat `.mimry/`, caches, and generated files as support artifacts, never as the place to fix a bug.
- Never paste secret values into context or reports; MIMRY skips secret-looking files and redacts feedback.
- Output is plain ASCII when piped (`OK`, `!`, `x` marks), and exit codes are meaningful: `0` ok, `1` not set up, `2` stale or an error.
  `mimry route "<task>" --json` returns structured data.

## Connect MIMRY to your agent

Agents can call MIMRY directly as an MCP server (stdio transport, command `mimry-mcp`).
`mimry install --platform <agent>` registers it for you at user scope, so every project gets it: through the agent's own CLI for Claude Code, Codex, Gemini CLI, Factory Droid, Kimi, Copilot CLI, Amp and Devin, and in the config file for Kiro and OpenCode.
For other agents it prints the snippet to add in their MCP settings.
Pass `--no-mcp`, or set `MIMRY_NO_MCP_REGISTRATION=1`, to skip it; a global `mimry uninstall --platform <agent>` removes it.

To register it by hand instead:

Claude Code:

```bash
claude mcp add --scope user mimry -- mimry-mcp
```

Codex:

```bash
codex mcp add mimry -- mimry-mcp
```

Any other MCP client:

```json
{
  "mcpServers": {
    "mimry": { "command": "mimry-mcp" }
  }
}
```

Without installing anything first, `uvx mimry mcp` runs the same server straight from PyPI (`"command": "uvx", "args": ["mimry", "mcp"]`).
MIMRY is listed in the [official MCP Registry](https://registry.modelcontextprotocol.io/) as `io.github.jbacalso24/mimry`.

The server works on its current directory by default, and every tool accepts a `root` argument for another repo.

| MCP tool | What it does |
|---|---|
| `mimry_preflight` | Set up if needed, then write a context pack and return the top files for a task |
| `mimry_find` | Rank files for a query |
| `mimry_related` | Files related to a query through the graph |
| `mimry_semantic` | Fuzzy "something like this" search |
| `mimry_symbol` | Look up a function, class, or other symbol by name |
| `mimry_context` | Write a context pack for a task |
| `mimry_explain` | Relevant files, key symbols, and how they connect |
| `mimry_path` | Shortest relationship path between two files or symbols |
| `mimry_why` | Why a file ranks for a query |
| `mimry_route` / `mimry_brief` | Recommend an agent role and write a role-specific brief |
| `mimry_status` / `mimry_refresh` / `mimry_reindex` / `mimry_init` | Index health and maintenance |
| `mimry_feedback` | Record which files helped a task |
| `mimry_list_adapters`, `mimry_digest`, `mimry_plan_*` | Adapters, canonical index digest, read-only plan trees |

### Teach your agent to use it

MIMRY can install a skill (instruction bundle) that teaches an agent the workflow above:

```bash
mimry install --project --platform claude-code --always-on --hooks
```

- `--project` installs into this repo only; omit it to install for every project.
- `--always-on` adds a short rules block to the agent's always-read file, such as `AGENTS.md`.
- `--hooks` adds a hook that reminds the agent once per session, on its first search, that MIMRY is available.

Supported platforms: `claude-code`, `codex`, `opencode`, `kilo`, `aider`, `copilot`, `claw`, `droid`, `trae`, `trae-cn`, `hermes`, `kiro`, `gemini`, `agents`, `amp`, `devin`, `antigravity`, `kimi`, `pi`, `codebuddy`.
Run `mimry install --list-platforms` for aliases, `mimry install --project --platform <name> --status` to check an install, and `mimry uninstall --project --platform <name> --always-on --hooks` to remove it.

## Why MIMRY?

- **Agents find the right file first.**
  On MIMRY's frozen retrieval benchmark, 94% of the files a task needs appear in its top 5 results (recall@5 0.94, nDCG@5 0.84).
- **It stays fast on large repos.**
  Like git, it trusts unchanged file metadata, so a reindex with no changes takes about three seconds on a 3,000-file repo, and a real reindex re-parses only what changed.
- **Answers carry evidence.**
  Every result says why it ranked, and `mimry why` and `mimry path` show the actual relationships behind it.
  Unresolvable imports are dropped rather than guessed.
- **It learns from use.**
  Feedback about files agents opened, changed, missed, or ignored becomes an explainable ranking signal.
- **It is private by default.**
  Everything stays local, semantic search uses a deterministic local method (`local-hash-v1`), and credential files are skipped.
- **It saves tokens.**
  On 44 held-out pull requests from six open-source repositories, a scripted agent using MIMRY read 40% fewer tokens than one using grep, and read every source file the change modified in 31 tasks to grep's 28 ([token benchmark](benchmarks/tokens/README.md)).
  When both agents read `sed` windows around `grep -n` matches instead of whole files, MIMRY read 37% fewer tokens and located 29 tasks to grep's 27.
  Results are compact summaries and relative paths, never full source dumps.

## Commands

Every command answers the same way: one line that says what happened, a few indented details, and a next step only when something needs doing.
Add `--verbose` (`-v`) to any command for paths, scores, and raw ranking reasons.

| Command | Use it to |
|---|---|
| `mimry preflight "<task>"` | Start a task: context pack plus the files to read first |
| `mimry find "<query>"` | Rank files for a query (`--semantic` blends in fuzzy matches) |
| `mimry related "<query>"` | Find files connected through the graph |
| `mimry semantic "<query>"` | Fuzzy recall for "I remember something like..." |
| `mimry symbol <name>` | Find where a symbol or Markdown heading is defined, or a file by name |
| `mimry context "<task>"` | Write a context pack without the preflight checks |
| `mimry explain "<task>"` | Files, key symbols, and how they connect |
| `mimry path <a> <b>` | Shortest relationship path between two files or symbols |
| `mimry why <file> --query "<task>"` | Why a file ranks for a query |
| `mimry route "<task>"` | Recommend an agent role (`--json` for tools) |
| `mimry brief "<task>" --agent <role>` | Write a role-specific brief |
| `mimry init` / `mimry index` | Set up a repo and build the index |
| `mimry reindex` / `mimry refresh` | Update the index (`--full` re-parses everything) |
| `mimry status` | Is the index current? (`--verify` re-hashes every file) |
| `mimry watch` | Poll for changes and refresh when they settle |
| `mimry git-hooks` | Keep the index fresh with git hooks (`install`/`uninstall`/`status`) |
| `mimry feedback ...` | Record what helped; `feedback stats`, `list`, `show <id>` read it back |
| `mimry roots` | List indexed folders on this machine (`--prune` forgets deleted ones) |
| `mimry plan ...` | Store and render explicit plan trees |
| `mimry mcp` | Run the MCP server (same as `mimry-mcp`) |
| `mimry adapters` | List the file-type adapters |
| `mimry install` / `mimry uninstall` | Manage agent skills |
| `mimry digest` | Print a canonical digest of the index |
| `mimry report` | Print the graph report, with its Suggested Questions |
| `mimry export --format {html,graphml,cypher,obsidian}` | Write the graph as an HTML viewer, GraphML, Cypher, or an Obsidian vault |
| `mimry cache wipe --current` | Delete this repo's cached index |

Use `mimry --root /path/to/repo <command>` to target another repo.

Keeping an index current:

```text
$ mimry status
! my-app is out of date - 1 file changed, 1 new since the last index
    changed  src/auth/session.py
    new      src/auth/magic_link.py
  Indexed 5 minutes ago · at commit d7bf046
  3,105 files · 6,859 symbols · 10,749 links
  Run `mimry reindex` to update it.

$ mimry reindex
✓ Updated the index for my-app in 2.2s
  1 file changed, 1 added
    changed  src/auth/session.py
    added    src/auth/magic_link.py
  3,106 files · 6,871 symbols · 10,762 links
```

Colour is used only on an interactive terminal, and `NO_COLOR=1` turns it off.
Piped and captured output always uses plain ASCII marks, so scripts and agents see the same text on every platform.

## What it builds

```text
project-root/
├─ .mimry/                  # local settings, gitignored
│  └─ mimry-out/            # generated output for agents and humans
│     ├─ context/latest.md  # latest context pack
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

A context pack contains the query and index freshness, ranked files with reasons, symbol hints, relationships, a suggested reading order, likely edit surfaces versus support files, risk notes for generated or sensitive paths, verification commands from the project's manifests and docs, and a final-report checklist.
It uses relative paths and never includes full source or secret values.
See [`docs/context-packs.md`](docs/context-packs.md) for the contract.

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

Languages parsed: Python, JavaScript, JSX, TypeScript, TSX, Go, Rust, C#, Java, PHP, C, C++, Ruby, Kotlin, Scala, and Swift.
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

Two opt-in commands refresh the index for you; neither runs by default.

- `mimry watch [--interval SECONDS]` polls in the foreground (every 2 seconds by default) and refreshes once a burst of edits settles.
- `mimry git-hooks install` adds post-commit, post-merge, post-checkout, and post-rewrite hooks that refresh in the background, so `git commit` returns immediately.
  Running it again is safe, `mimry git-hooks status` shows what is installed, and `mimry git-hooks uninstall` removes only MIMRY's lines.

If a hook manager such as husky owns your hooks, add `mimry refresh` to its post-commit hook instead.

## Feedback learning

`mimry feedback` records which files an agent actually used after a task.
It is stored in the repo's local cache, is not telemetry, and becomes a modest, explainable ranking signal for similar future queries.

```bash
mimry feedback --query "fix board card click" \
  --context .mimry/mimry-out/context/latest.md \
  --opened src/features/boards/api.ts \
  --changed src/app/api/bridge/route.ts \
  --missed bridge/fastapi_app.py \
  --ignored README.md \
  --verification "npm run build passed" \
  --outcome passed
```

Exact filename, symbol, graph, and source evidence stay primary.
Feedback breaks ties and recovers previously missed files; it does not override source-truth signals.

## Recursive plan trees

`mimry plan` stores explicit, user-authored plan trees: one root with ordered children, each of which can be split again.
It stores and renders the tree only; it does not generate plans, execute work, or track progress.

```bash
mimry plan new "Ship feature" --name ship-feature
mimry plan split <plan-id> <root-node-id> --child "Design" --child "Implement"
mimry plan tree <plan-id>          # also --json or --md
mimry plan check <plan-id>
mimry plan list
```

```text
Ship feature [node-...]
|-- Design [node-...]
`-- Implement [node-...]
```

`split` works on leaves only, and repeated `--child` arguments set sibling order.
Trees are canonical versioned JSON under `.mimry/plans/`, written atomically.
IDs, projections, and the semantic SHA-256 from `mimry plan digest` exclude paths, timestamps, locale, and formatting.
MCP exposes read-only parity (`mimry_plan_tree`, `mimry_plan_check`, `mimry_plan_digest`, `mimry_plan_list`); changing a plan is CLI-only.

## Good roots vs bad roots

MIMRY works best when one root is one meaningful working context: a repo or a project folder.

Good roots: `~/Documents/project-a`, `C:\Users\you\Documents\project-a`, `D:\Work\active-product`.

Bad roots: `/`, `~`, `~/.config`, `~/.cache`, `C:\`, `C:\Users\you`, `AppData`, `node_modules`.

Whole-drive or whole-home indexing is noisy, slow, and likely to include secrets, caches, and unrelated projects.
For a personal "global" memory, make a curated folder such as `~/Workspace` and put only useful projects and docs in it.

## Local-first safety

- Source files remain the final truth.
- Context packs use relative paths and summaries, not full source.
- Semantic search defaults to the deterministic local `local-hash-v1`; nothing is sent to a remote embedding service.
- Feedback is local metadata, not telemetry, and secret-looking values are redacted before they are stored.
- Scanner rules skip common credential files.
- Tester-owned `acceptance_tests/` trees stay out of ordinary index surfaces by default.
  This is cooperative isolation, not secrecy: anyone with the repo can still open those files.

### Cache generation and lock safety

Index publication, feedback writes, readers, generation cleanup, and `mimry cache wipe --current` share one per-root operation lock.
POSIX readers share it; Windows readers take it exclusively because `msvcrt.locking` has no shared mode.
Lock waits are bounded to 10 seconds by default; set `MIMRY_LOCK_TIMEOUT_SECONDS` to raise the bound.
Timeout errors include the lock path and holder metadata, so a stuck process can be diagnosed without deleting lock files blindly.

Published indexes are immutable generations.
Startup and successful publication remove abandoned staging trees and keep only the current and last-known-good generations.
Generation manifests checksum immutable sidecars, and SQLite semantic rows carry the same generation identity plus a content checksum.
`mimry cache wipe --all` is disabled until MIMRY has a proven cache-global writer protocol; use `--current` instead.

## Structured adapters

Adapters teach MIMRY about common project surfaces: `python-ast`, `typescript-ast`, `config-manifest`, `nextjs-app-router`, `fastapi`, `react-native-expo`, `sql-schema`, `markdown-docs`, and `generic-text`.
Run `mimry adapters --verbose` to see what each extracts.

## Supported platforms

MIMRY targets Windows, Linux, and macOS on Python 3.11 to 3.13, and CI runs the full suite on all nine combinations.
Paths are handled POSIX-style internally, and platform-specific system calls are capability-guarded rather than assumed.
Handled explicitly, because each was a real bug:

- `Path.home()` resolves from `USERPROFILE` on Windows, not `HOME`.
- `fsync` needs a writable descriptor on Windows, and `st_ctime` there is creation time.
- `os.utime(follow_symlinks=False)` and byte-range lock semantics are capability-guarded.
- Human CLI output degrades Unicode safely on strict cp1252 consoles, while JSON, Markdown, and index artifacts stay UTF-8.
- Subprocesses never inherit stdin, which under the MCP stdio server is the JSON-RPC channel.

## Support and releases

CI runs on every push and pull request: lint, tests, an end-to-end determinism matrix, an unlocked extracted-sdist parser suite, and a clean-wheel smoke test on each platform, plus jobs that compare determinism evidence across platforms and gate the retrieval benchmark.
A separate workflow audits the locked runtime dependencies for known vulnerabilities whenever they change, and weekly.
Releases are cut by pushing a `vX.Y.Z` tag, which builds reproducible artifacts and publishes them to PyPI and GitHub Releases.
See [`CHANGELOG.md`](CHANGELOG.md) for release notes and [`RELEASING.md`](RELEASING.md) for the release checks.
Report security issues privately as described in [`SECURITY.md`](SECURITY.md).

## Contributing

Contributions are welcome: bug fixes, portability and privacy improvements, documentation, and retrieval changes backed by the benchmark.
Open an issue first for large features or changes to privacy defaults.
See [`CONTRIBUTING.md`](CONTRIBUTING.md) for setup and the checks to run before opening a pull request.

## Development

```bash
git clone https://github.com/jbacalso24/mimry.git
cd mimry
uv sync
uv run ruff format .
uv run ruff check .
uv run pytest -q
```

Install the CLI from your checkout, so edits take effect immediately:

```bash
uv tool install --editable .
```

An editable install picks up code changes, but not dependency changes.
After a pull that changes the dependencies in `pyproject.toml`, run `uv tool upgrade mimry` to install them; `mimry status` names any that are missing.
Do not reinstall with `uv tool install --force` instead: it deletes the whole tool environment first, and on Windows, while any `mimry-mcp` is running, the delete stops at the first locked file and leaves a half-removed install.

Install the Git hooks with `uv run pre-commit install`.
Run `uv run mimry-integration-smoke` for a real MCP stdio round trip plus isolated Claude Code and Codex registration checks; it never touches your normal agent configuration.

## Status

MIMRY is a public alpha: early but usable for local, per-project agent memory, and its interfaces may still change before 1.0.
It is not a whole-computer brain and should not be pointed at entire drives or home directories.

## License

MIMRY is free and open source under the [MIT License](LICENSE).
Feel free to use it in your own projects, personal or commercial, and to modify, fork or build on it.
Keep the copyright and license notice when you redistribute it.
