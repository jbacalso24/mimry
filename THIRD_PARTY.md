# Third-party notices

MIMRY's graph engine is its own (`src/mimry/core/`). The remaining third-party
dependencies are runtime libraries, not infrastructure MIMRY defers to.

## tree-sitter and tree-sitter-language-pack

Used for parsing source files into symbols, imports, and call sites.

- `tree-sitter` (MIT)
- `tree-sitter-language-pack`, pinned to `1.12.2` (MIT)

The pin is deliberate: `1.13.x` changes the TSX grammar shape and breaks exported
function and component extraction. Do not raise it without re-running the locked and
unlocked artifact tests.

The language pack bundles every grammar MIMRY uses -- Python, JavaScript, TypeScript,
TSX, Go, Rust and C# -- so adding a supported language costs a table entry in
`src/mimry/core/languages.py` rather than a new dependency.

## fastmcp

Used for the MCP server entrypoint (`mimry-mcp`) that exposes MIMRY's tools to coding
agents.

- `fastmcp` (Apache-2.0)

## Historical note

Through 0.1.x development MIMRY used Graphify as its graph engine, pinned by Git commit
and tracked as a submodule under `vendor/graphify`.

- Upstream repository: https://github.com/safishamsi/graphify
- Python package: `graphifyy`
- License: MIT
- Copyright: Copyright (c) 2026 Safi Shamsi

It was replaced by the native engine after measurement rather than on principle. On
MIMRY's own retrieval benchmark the native engine scored better on every quality metric
and reached a queryable indexed graph in roughly half the wall-clock time.
`benchmarks/baseline.graphify.json` retains the recorded Graphify numbers so the
comparison stays auditable after the dependency is gone.

Removing it also dropped the direct Git dependency reference from package metadata,
which was the reason 0.1.x could not be published to a PyPI-style index.
