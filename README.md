# MIMRY

**MIMRY = Modular Intelligence Memory for Repos**

Python-first, Graphify-powered local intelligence memory CLI for repos, folders, humans, and agents.

MVP loop:

```bash
mimry init
mimry refresh
mimry status
mimry find "auth"
mimry context "fix auth bug"
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
mimry init
mimry refresh
mimry status
mimry find "auth login token"
mimry context "understand auth flow"
```

`mimry init` creates local generated metadata under `.mimry/`. When the target root is inside an initialized Git worktree, MIMRY safely verifies or appends the appropriate `.mimry/` ignore entry to that worktree's `.gitignore` without replacing existing content.

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
generic-text
```

## Graphify integration

Graphify is pinned in `pyproject.toml`/`uv.lock` and installed by `uv sync`. A vendored submodule may also exist under `vendor/graphify` for direct Graphify integration work.

Fetch the optional submodule when working directly on Graphify integration:

```bash
git submodule update --init --recursive
```

MIMRY defaults to local/no-network indexing behavior. See `THIRD_PARTY.md` for Graphify attribution and integration boundaries.
