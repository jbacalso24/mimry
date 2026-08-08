<p align="center">
  <img src="assets/banner.svg" alt="MIMRY — Local repo memory for coding agents" width="100%">
</p>

<h1 align="center">MIMRY</h1>

<p align="center">
  <strong>Local repo memory for coding agents.</strong>
  <br />
  Build context packs, graph maps, semantic recall, and feedback-ranked search from your own codebase.
</p>

<p align="center">
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-blue">
  <img alt="License MIT" src="https://img.shields.io/badge/license-MIT-green">
  <img alt="Local first" src="https://img.shields.io/badge/local--first-yes-purple">
  <img alt="CLI" src="https://img.shields.io/badge/interface-CLI-black">
  <img alt="MCP" src="https://img.shields.io/badge/agent-MCP-orange">
</p>

<p align="center">
  <a href="#quickstart"><strong>Quickstart</strong></a>
  ·
  <a href="#why-mimry"><strong>Why MIMRY?</strong></a>
  ·
  <a href="#what-it-builds"><strong>What it builds</strong></a>
  ·
  <a href="#mcp-for-agents"><strong>MCP</strong></a>
  ·
  <a href="#good-roots-vs-bad-roots"><strong>Good roots</strong></a>
  ·
  <a href="#development"><strong>Development</strong></a>
</p>

---

MIMRY gives agents a focused memory layer for a repo or project folder. Instead of opening random files, rereading the same tree, or guessing where logic lives, an agent can ask MIMRY for a compact evidence-backed handoff before it edits anything.

```bash
cd /path/to/your/project
mimry preflight "fix auth bug"
```

MIMRY checks freshness, refreshes when needed, ranks likely files, and writes a context pack for the agent:

```text
Context: .mimry/mimry-out/context/latest.md
Top files:
1. src/auth/session.py
2. src/app/login/page.tsx
3. tests/test_auth.py
```

MIMRY is local-first. Source files, tests, and real build output remain the final truth.

## Why MIMRY?

Coding agents usually start cold. They need to discover project structure, find the right files, understand relationships, and avoid touching generated or risky paths. That wastes time and creates bad edits.

MIMRY turns a local repo into a small intelligence layer:

- **Context packs** for agent handoffs
- **Task routing and role-aware briefs** for agent orchestration
- **File and symbol indexes** for quick navigation
- **Relationship maps** for code/document relationships
- **Local semantic recall** for fuzzy “I remember something like…” queries
- **Feedback ranking** from what agents actually opened, changed, missed, or ignored
- **MCP tools** so coding agents can query MIMRY directly
- **Privacy-first behavior**: no remote embeddings by default, no source dumps in context packs, and secret-looking feedback values are redacted before persistence
- **Cooperative test isolation**: tester-owned `acceptance_tests/` trees stay outside ordinary index surfaces by default; this is not secrecy, because repository users can still open those files directly

Use it when you want agents to answer:

- “What files matter for this task?”
- “Where is this flow probably implemented?”
- “What should I read first?”
- “What changed last time a similar task was done?”
- “What verification commands are likely relevant?”

## Quickstart

MIMRY is a Python package with two CLI entrypoints:

- `mimry` — main CLI
- `mimry-mcp` — MCP server for agent integrations

Requirements:

- Python `>=3.11` (required CI currently covers 3.11, 3.12, and 3.13)
- [`uv`](https://docs.astral.sh/uv/)
- Git (for repo analysis)

### Supported platforms

MIMRY targets **Windows, Linux, and macOS**. Paths are handled POSIX-style
internally regardless of host, and platform-specific syscalls are capability-guarded
rather than assumed.

Automated CI currently runs `ubuntu-latest` and `macos-latest`. Windows is supported
and actively tested locally, but is not yet in the CI matrix — so Windows-specific
regressions can land without CI catching them. If you develop on Windows, run
`uv run pytest -q` before opening a PR. Adding `windows-latest` to CI is tracked work;
it needs the workflow's POSIX-only steps (`.venv/bin/python`, `/tmp`, tarball globbing)
made portable first.

MIMRY 0.1.x is distributed internally from an authorized checkout or as a direct wheel artifact. See [`RELEASING.md`](RELEASING.md) for the supported channel and exact commands.

Install from a checkout:

```bash
git clone <mimry-repo-url>
cd mimry
uv sync
uv run pytest -q
uv tool install --editable . --force
```

Verify the install:

```bash
mimry --help
mimry status
uv tool list
```

Expected `uv tool list` output includes:

```text
mimry v0.1.0
- mimry
- mimry-mcp
```

## First run in a project

Use MIMRY inside a local repo or meaningful project folder:

```bash
cd /path/to/your/project
mimry init
mimry refresh
mimry status
mimry context "understand auth flow"
```

For agents, prefer the one-command preflight workflow:

```bash
mimry preflight "fix auth bug"
```

`preflight` initializes the root if needed, checks index freshness, refreshes only when state is missing or stale, generates `.mimry/mimry-out/context/latest.md`, prints top files, and reminds the agent to read the context pack before opening source files.

## What it builds

After `mimry init` and `mimry refresh`, a project gets this shape:

```text
project-root/
├─ .mimry/                  # hidden local control/config
│  └─ mimry-out/            # generated MIMRY output for agents/humans
│     ├─ context/latest.md  # latest context pack
│     └─ graph/             # native relationship graph artifacts
└─ source files...
```

MIMRY also stores local indexes in the user cache directory. Those indexes are generated artifacts, not source of truth.

A context pack includes:

- query and status summary
- index and graph freshness
- ranked relevant files with reason labels
- symbol/entity hints when indexed
- graph relationship and community signals when available
- suggested reading order
- likely edit surfaces vs support files
- risk notes for generated/cache/privacy-sensitive paths
- suggested verification commands from project manifests and docs
- final report checklist for agents

Context packs remain relative-path-first and never dump full source or secret values. See [`docs/context-packs.md`](docs/context-packs.md) for the section contract.

## CLI examples

Find likely files:

```bash
mimry find "auth login token"
```

Generate an agent handoff:

```bash
mimry context "debug checkout redirect"
```

Route a task to an agent role and write a focused brief:

```bash
mimry route "fix FastAPI auth migration bug"
mimry route "fix FastAPI auth migration bug" --json
mimry brief "fix FastAPI auth migration bug" --agent backend
```

`route` recommends one of `backend`, `frontend`, `mobile`, `reviewer`, `qa`, `docs`, `tooly`, or `general`, with confidence, why, suggested context-pack labels, likely files, risk level/severity, risk/approval gates, verification hints, and a pasteable next command. `--json` prints the same structured payload for agents/tools. `brief` writes `.mimry/mimry-out/context/brief-<agent>.md` without dumping source contents.

Explain why something ranked:

```bash
mimry why src/auth/session.py --query "login auth bug"
```

Find a graph relationship path:

```bash
mimry path "ui hook" "sqlite cache"
```

Explain a task/debug area:

```bash
mimry explain "login route bug"
```

Use local semantic recall:

```bash
mimry semantic "vague thing I remember"
mimry find "auth redirect weirdness" --semantic
mimry context "payment flow bug" --semantic
```

Target another root explicitly:

```bash
mimry --root /path/to/repo status
```

## Feedback learning

`mimry feedback` records local operational metadata about what an agent actually used after a task. It is not telemetry and is not durable user memory. It is stored in the current root's local SQLite cache and used as a modest explainable ranking signal for future similar queries.

Example:

```bash
mimry feedback --query "fix board card click bridge unavailable" \
  --context .mimry/mimry-out/context/latest.md \
  --opened src/features/boards/api.ts,src/app/api/bridge/[...path]/route.ts \
  --changed src/app/api/bridge/[...path]/route.ts \
  --missed bridge/fastapi_app.py \
  --ignored README.md \
  --verification "npm run build passed" \
  --outcome passed
```

Read feedback later:

```bash
mimry feedback stats
mimry feedback list --limit 10
mimry feedback show <feedback_id>
```

Feedback can add labels such as:

- `feedback changed-file boost`
- `feedback opened-file boost`
- `feedback missed-file recovery boost`
- `feedback ignored suggestion downrank`

Exact filename, symbol, graph, framework, and source evidence remain primary. Feedback helps break ties and recover previously missed files; it should not override source-truth signals.

## MCP for agents

Expose MIMRY to coding agents through MCP:

```bash
mimry-mcp
```

Available MCP-style workflows include:

- status checks
- index refresh
- file search
- related file discovery
- symbol search
- context-pack generation
- adapter listing
- task routing (`mimry_route`)
- role-aware brief generation (`mimry_brief`)

This lets agents ask the local project memory for focused context instead of scraping the whole repo from scratch.

For a real stdio protocol round trip plus isolated Claude Code and Codex MCP registration checks, run:

```bash
uv run mimry-integration-smoke
```

The smoke is included in installed wheels as well as source artifacts. It creates a temporary repo/cache and temporary client config homes, passes clients a credential-free allowlisted environment, calls MIMRY tools through an actual FastMCP stdio client, asks Claude Code to health-check the server, and reads Codex's registered transport back as JSON. It never edits the operator's normal Claude/Codex configuration. Missing clients are reported as `UNVERIFIED`, not silently treated as passes; Herdr remains pane-only and is never simulated by this harness.

## Agent skill install

MIMRY can also install a MIMRY-owned skill/instruction bundle for coding agents. It installs MIMRY workflow rules only.

List supported platforms:

```bash
mimry install --list-platforms
```

Install globally for an agent profile:

```bash
mimry install --platform claude-code
mimry install --platform codex
mimry install --platform hermes
mimry install --platform agents
mimry install --platform opencode
mimry install --platform kilo
mimry install --platform aider
mimry install --platform copilot
mimry install --platform claw
mimry install --platform droid
mimry install --platform trae
mimry install --platform trae-cn
mimry install --platform kiro
mimry install --platform gemini
mimry install --platform amp
mimry install --platform devin
mimry install --platform antigravity
mimry install --platform kimi
mimry install --platform pi
mimry install --platform codebuddy
```

Install into the current project instead:

```bash
mimry install --project --platform codex
```

Project installs write skill files and focused references, for example:

```text
.codex/skills/mimry/SKILL.md
.codex/skills/mimry/references/workflow.md
.codex/skills/mimry/references/commands.md
.codex/skills/mimry/references/mcp.md
.codex/skills/mimry/references/feedback.md
.codex/skills/mimry/references/safety.md
```

For project-scoped installs, add always-on instructions so the agent is reminded to use MIMRY before broad search:

```bash
mimry install --project --platform codex --always-on
```

For supported platforms, also add PreToolUse hooks that nudge the agent before broad search/file exploration:

```bash
mimry install --project --platform codex --hooks
mimry install --project --platform claude-code --hooks
```

Check install health and repair by rerunning install with the same options:

```bash
mimry install --project --platform codex --status
mimry install --project --platform codex --always-on --hooks
```

Remove an install:

```bash
mimry uninstall --project --platform codex --always-on --hooks
```

`claude` is accepted as an alias for `claude-code`, `skills` for `agents`, `openclaw` for `claw`, and `factory` for `droid`. The Claude target means **Claude Code**, not the Claude web app.

The installed skill tells agents to run `mimry preflight`, read `.mimry/mimry-out/context/latest.md`, use focused MIMRY queries before broad search, verify against real source/tests/build output, and record `mimry feedback` after work.

## Good roots vs bad roots

MIMRY works best when one root equals one meaningful working context.

Good roots:

```text
/home/you/Documents/project-a
/home/you/Documents/product-workspace
/home/you/.hermes/hermes-agent
/Users/you/Documents/project-a
C:\Users\you\Documents\project-a
D:\Work\active-product
```

Bad roots:

```text
/
/home/you
/home/you/.config
/home/you/.cache
/Users/you
C:\
C:\Users\you
C:\Users\you\AppData
node_modules
```

Avoid whole-drive or whole-home indexing. It is noisy, slower, and more likely to include secrets, caches, browser data, dependency folders, and unrelated old projects.

If you want a personal “global” memory, create a curated folder such as:

```text
~/Workspace
~/Documents/Active
D:\Work\Active
```

Put only useful projects and docs there.

## Local-first safety

MIMRY is designed around local project memory:

- source files remain the final truth
- context packs use relative paths and summaries, not full source dumps
- semantic search defaults to deterministic local `local-hash-v1`
- feedback is local metadata, not telemetry
- secret-looking feedback values are redacted before persistence
- scanner/security rules skip common credential files
- generated/cache paths are treated as support artifacts, not source fixes

MIMRY narrows context. It does not replace reading source files, running tests, or checking real build output.

### Cache generation and lock safety

Index publication, feedback writes, readers, generation cleanup, and `mimry cache wipe --current` use one stable per-root operation lock. POSIX readers share it; Windows readers intentionally take it exclusively because `msvcrt.locking` has no shared mode. Lock waits are bounded to 10 seconds by default; set `MIMRY_LOCK_TIMEOUT_SECONDS` to a positive number when a slower operation needs a larger bound. Timeout errors include the lock path and available holder metadata so a stuck process can be diagnosed without deleting lock files blindly.

After `mimry cache wipe --current`, status truthfully reports a missing index and the normal `mimry index` / `mimry refresh` path rebuilds it. `mimry cache wipe --all` is disabled until MIMRY has a proven cache-global writer coordination protocol; use the per-root wipe instead.

Published indexes are immutable generations. Startup and successful publication remove abandoned staging/orphan trees while retaining only the pointer-current and last-known-good generations. Generation manifests checksum immutable sidecars, and SQLite semantic rows carry the same generation identity plus a deterministic content checksum.

Locking uses advisory `flock` on POSIX systems, including macOS. Windows uses `msvcrt.locking` over one byte as a best-effort advisory equivalent; Windows does not provide POSIX inode/`flock` semantics, so native Windows crash/rename behavior remains a platform-specific verification boundary.

## Structured adapters

MIMRY includes structured adapters for common project surfaces. Check active adapters with:

```bash
mimry adapters --active-only
```

Current adapters include:

```text
python-ast
typescript-ast
config-manifest
nextjs-app-router
fastapi
react-native-expo
sql-schema
markdown-docs
generic-text
```

## Graph engine

MIMRY builds its own relationship graph in `src/mimry/core/`. Indexing parses each file
once and derives three kinds of edge from what it already read:

- `defines` -- a file defines a symbol
- `imports` -- an import statement resolved to a real file in the repo
- `calls` -- a call site resolved to a symbol, in the same file or through an import

Imports that cannot be resolved unambiguously are dropped rather than guessed, so a
relationship shown by `mimry path` or `mimry why` is one MIMRY can actually evidence.
Nodes are grouped into communities by deterministic label propagation, and `graph.json`
is byte-identical across rebuilds of unchanged sources.

Languages covered: Python, JavaScript, TypeScript, JSX, TSX, Go, Rust, and C#. Adding a
language is a table entry in `core/languages.py`, not new code -- every grammar ships in
the already-pinned `tree-sitter-language-pack`.

Artifacts land in `.mimry/mimry-out/graph/`: `graph.json`, `GRAPH_REPORT.md`, and
`manifest.json`. They are written during `mimry index`; there is no separate build step.

## Support and releases

Required CI covers Python 3.11–3.13 on current GitHub-hosted Ubuntu and macOS runners. Windows remains best-effort until it joins the required matrix. Releases are manual internal direct artifacts: CI builds, performs an unlocked functional test from the extracted sdist, and clean-installs the wheel, but has no publishing credentials or publish step.

See [`CHANGELOG.md`](CHANGELOG.md) for release notes and [`RELEASING.md`](RELEASING.md) for the exact artifact and clean-wheel checks.

## Development

Install development dependencies:

```bash
uv sync
```

Run checks:

```bash
uv run ruff format .
uv run ruff check .
uv run pytest -q
```

Install local Git hooks:

```bash
uv run pre-commit install
uv run pre-commit run --all-files
```

Install the CLI from your checkout:

```bash
uv tool install --editable . --force
```

## Status

MIMRY is early but usable for local, per-project agent memory. It is not a whole-computer brain and should not be pointed at entire drives or home directories.
