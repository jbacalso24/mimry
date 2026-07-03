# MIMRY

**MIMRY = Modular Intelligence Memory for Repos**

Python-first, Graphify-powered local intelligence memory CLI for repos, folders, humans, and agents.

MVP loop:

```bash
mimry preflight "fix auth bug"
```

Manual loop:

```bash
mimry init
mimry refresh
mimry status
mimry find "auth"
mimry context "fix auth bug"
mimry feedback --query "fix auth bug" --context .mimry/context/latest.md --opened src/auth/session.py --changed src/auth/session.py --verification "uv run pytest -q passed" --outcome passed
mimry explain "login route bug"
mimry path "ui hook" "sqlite cache"
mimry why src/auth/session.py --query "login auth bug"
```

MIMRY is local-first. Source files remain the final truth.

## Install

MIMRY uses modern `pyproject.toml` packaging with Hatchling and exposes global CLI commands through `[project.scripts]`.

From a fresh machine:

```bash
git clone https://github.com/jbacalso24/mimry.git
cd mimry
uv sync
uv run pytest -q
uv tool install --editable . --force
```

Submodules are optional for normal CLI/test usage. MIMRY installs the pinned Graphify dependency from Git during `uv sync`.

If you specifically want the vendored Graphify checkout under `vendor/graphify`:

```bash
cd mimry
git submodule update --init --recursive
```

Verify the global CLI install:

```bash
command -v mimry
mimry --help
uv tool list
```

Install the local Git hooks for development:

```bash
uv run pre-commit install
uv run pre-commit run --all-files
```

The pre-commit hook runs `ruff check` and `ruff format --check` before commits.

Expected `uv tool list` output includes:

```text
mimry v0.1.0
- mimry
- mimry-mcp
```

## Use

Use MIMRY from inside any local repo. MIMRY defaults to the current working directory:

```bash
cd /path/to/repo
mimry preflight "understand auth flow"
```

`mimry preflight <task>` is the recommended one-command agent discipline workflow. It initializes the root if needed, checks index freshness and Graphify artifact health, refreshes only when state is missing/stale (or when `--force-refresh` is passed), generates `.mimry/context/latest.md`, prints the top files, and reminds agents to read the context pack before opening files.

The context pack is an evidence-grade agent handoff, not just a file list. It includes:

- query and status summary (root, index freshness, Graphify freshness, refresh action)
- relevant files with scores, reason labels, adapter evidence, and likely roles
- relevant symbols/entities when indexed
- Graphify relationship paths/community/report signals when current; explicit degradation when missing/stale
- suggested reading order with rationale
- likely edit surfaces vs non-edit supporting files
- risk notes for generated/cache paths, privacy-sensitive files, broad dirty work, docs/tests/config support files
- suggested verification commands detected from config manifests and repo docs
- source-of-truth reminder and final report checklist for agents

MIMRY context packs remain relative-path-first and never dump full source or secret values. Source files, tests, and real build output remain the final truth. See [`docs/context-packs.md`](docs/context-packs.md) for the section contract.

Manual commands remain available:

```bash
cd /path/to/repo
mimry init
mimry refresh
mimry status
mimry find "auth login token"
mimry context "understand auth flow"
```

Agent-debugging commands use the same Graphify artifacts and MIMRY indexed facts as `find`, `related`, and `context`:

- `mimry explain "<task/debug query>"` prints top ranked files, symbols/entities, Graphify relationship evidence, likely source of truth, and verification hints.
- `mimry path "<source file/symbol/query>" "<target file/symbol/query>"` finds a real Graphify relationship path when one exists. If no path resolves, it says so, shows candidate surfaces, and suggests fallback queries instead of inventing a connection.
- `mimry why "<file or symbol>" --query "<task>"` explains the filename/symbol/Graphify/community/adapter/config signals that caused a surface to rank for a task.

These commands are intentionally concise and agent-readable. Refresh first when Graphify or the index is stale:

```bash
mimry refresh
```

`mimry init` creates local generated metadata under `.mimry/`. When the target root is inside an initialized Git worktree, MIMRY safely verifies or appends the appropriate `.mimry/` ignore entry to that worktree's `.gitignore` without replacing existing content.

## Agent usage feedback

`mimry feedback` records local operational metadata about what an agent actually used after a task. It is not Hermes Memory Intake, not durable user memory, and not telemetry. MIMRY stores only bounded task metadata and paths in the current root's local SQLite cache; it does not store full source contents, parse secrets, call remote models, or send feedback over the network.

CLI example:

```bash
mimry feedback --query "fix board card click bridge unavailable" \
  --context .mimry/context/latest.md \
  --opened src/features/boards/api.ts,src/app/api/bridge/[...path]/route.ts \
  --changed src/app/api/bridge/[...path]/route.ts \
  --missed bridge/fastapi_app.py \
  --ignored README.md \
  --verification "npm run build passed" \
  --outcome passed
```

JSON input is also supported:

```bash
mimry feedback --json feedback.json
```

```json
{
  "query": "fix board card click bridge unavailable",
  "context_path": ".mimry/context/latest.md",
  "suggested": ["src/app/api/bridge/[...path]/route.ts", "bridge/fastapi_app.py"],
  "opened": ["src/features/boards/api.ts", "src/app/api/bridge/[...path]/route.ts"],
  "changed": ["src/app/api/bridge/[...path]/route.ts"],
  "missed": ["bridge/fastapi_app.py"],
  "ignored": ["README.md"],
  "verification": [{"command": "npm run build", "status": "passed"}],
  "outcome": "passed",
  "notes": "Bridge proxy was source of truth."
}
```

Read commands:

```bash
mimry feedback stats
mimry feedback list --limit 10
mimry feedback show <feedback_id>
```

Feedback affects future `find`, `related`, `context`, `preflight`, `explain`, and MCP search/context results only as a modest explainable ranking signal when the new query overlaps a previous task query. It can add these labels: `feedback changed-file boost`, `feedback opened-file boost`, `feedback missed-file recovery boost`, and `feedback ignored suggestion downrank`. Exact filename/symbol/Graphify/framework evidence remains primary; feedback should help break ties and recover missed files, not override source-truth signals.

Agent final-report pattern: after verification, include the context query/path, files suggested/opened/changed/missed/ignored, verification outcome, then run `mimry feedback ...` so the next agent gets better local rankings.

You can still target another repo explicitly when needed:

```bash
mimry --root /path/to/repo status
```

Expose it to coding agents through MCP:

```bash
uv run mimry-mcp
```

Current structured adapters:

```bash
uv run mimry adapters --active-only
```

Active today:

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

Graphify is pinned in `pyproject.toml`/`uv.lock` and installed by `uv sync`. A vendored submodule may also exist under `vendor/graphify` for direct Graphify integration work.

Fetch the optional submodule when working directly on Graphify integration:

```bash
git submodule update --init --recursive
```

MIMRY defaults to local/no-network indexing behavior. See `THIRD_PARTY.md` for Graphify attribution and integration boundaries.
