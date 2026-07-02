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

## Local install / use

From a fresh machine:

```bash
git clone --recurse-submodules https://github.com/jbacalso24/mimry.git
cd mimry
uv sync
uv run pytest -q
uv run mimry --help
```

Use it on any local repo:

```bash
uv run mimry --root /path/to/repo init
uv run mimry --root /path/to/repo index
uv run mimry --root /path/to/repo status
uv run mimry --root /path/to/repo find "auth login token"
uv run mimry --root /path/to/repo context "understand auth flow"
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
