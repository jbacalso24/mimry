# MIMRY

**MIMRY = Modular Intelligence Memory for Repos**

Python-first, Graphify-powered local intelligence memory CLI for repos, folders, humans, and agents.

MVP loop:

```bash
mimry init
mimry index
mimry status
mimry find "auth"
mimry context "fix auth bug"
```

MIMRY is local-first. Source files remain the final truth.

## Install

MIMRY uses modern `pyproject.toml` packaging with Hatchling and exposes global CLI commands through `[project.scripts]`.

From a fresh machine:

```bash
git clone --recurse-submodules https://github.com/jbacalso24/mimry.git
cd mimry
uv sync
uv run pytest -q
uv tool install --editable . --force
```

If you already cloned without submodules:

```bash
cd mimry
git submodule update --init --recursive
uv sync
uv tool install --editable . --force
```

Verify the global CLI install:

```bash
command -v mimry
mimry --help
uv tool list
```

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
mimry index
mimry status
mimry find "auth login token"
mimry context "understand auth flow"
```

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
generic-text
```

## Graphify integration

Graphify is tracked as a pinned git submodule under `vendor/graphify` and wrapped through `src/mimry/graphify_core`.

Clone with submodules when working on Graphify integration:

```bash
git clone --recurse-submodules https://github.com/jbacalso24/mimry.git
# or, after clone
git submodule update --init --recursive
```

MIMRY defaults to local/no-network indexing behavior. See `THIRD_PARTY.md` for Graphify attribution and integration boundaries.
