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
Context: mimry-out/context/latest.md
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
- **File and symbol indexes** for quick navigation
- **Graphify relationship maps** for code/document relationships
- **Local semantic recall** for fuzzy “I remember something like…” queries
- **Feedback ranking** from what agents actually opened, changed, missed, or ignored
- **MCP tools** so coding agents can query MIMRY directly
- **Privacy-first behavior**: no remote embeddings by default, no source dumps in context packs, and secret-looking feedback values are redacted before persistence

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

- Python `>=3.11`
- [`uv`](https://docs.astral.sh/uv/)
- Git

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

`preflight` initializes the root if needed, checks index freshness, refreshes only when state is missing or stale, generates `mimry-out/context/latest.md`, prints top files, and reminds the agent to read the context pack before opening source files.

## What it builds

After `mimry init` and `mimry refresh`, a project gets this shape:

```text
project-root/
├─ .mimry/              # hidden local control/config
├─ mimry-out/           # visible generated output for agents/humans
│  ├─ context/latest.md # latest context pack
│  └─ graph/            # Graphify-derived relationship artifacts
└─ source files...
```

MIMRY also stores local indexes in the user cache directory. Those indexes are generated artifacts, not source of truth.

A context pack includes:

- query and status summary
- index and graph freshness
- ranked relevant files with reason labels
- symbol/entity hints when indexed
- Graphify relationship and community signals when available
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
  --context mimry-out/context/latest.md \
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

Exact filename, symbol, Graphify, framework, and source evidence remain primary. Feedback helps break ties and recover previously missed files; it should not override source-truth signals.

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

This lets agents ask the local project memory for focused context instead of scraping the whole repo from scratch.

## Agent skill install

MIMRY can also install a small MIMRY-owned skill/instruction bundle for coding agents. This is separate from Graphify's installer: MIMRY uses Graphify internally, but installs MIMRY workflow rules.

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
```

Install into the current project instead:

```bash
mimry install --project --platform codex
```

Project installs write skill files and focused references:

```text
.claude/skills/mimry/SKILL.md
.codex/skills/mimry/SKILL.md
.hermes/skills/mimry/SKILL.md
.agents/skills/mimry/SKILL.md

*/skills/mimry/references/workflow.md
*/skills/mimry/references/commands.md
*/skills/mimry/references/mcp.md
*/skills/mimry/references/feedback.md
*/skills/mimry/references/safety.md
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

`claude` is accepted as an alias for `claude-code`, and `skills` is accepted as an alias for `agents`. The Claude target means **Claude Code**, not the Claude web app.

The installed skill tells agents to run `mimry preflight`, read `mimry-out/context/latest.md`, use focused MIMRY queries before broad search, verify against real source/tests/build output, and record `mimry feedback` after work.

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

## Graphify integration

MIMRY uses Graphify-derived relationship artifacts for graph navigation and context evidence. Graphify is pinned in `pyproject.toml`/`uv.lock` and installed by `uv sync`.

Submodules are optional for normal CLI/test usage. If you specifically want the vendored Graphify checkout under `vendor/graphify`:

```bash
git submodule update --init --recursive
```

See [`THIRD_PARTY.md`](THIRD_PARTY.md) for Graphify attribution and integration boundaries.

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
