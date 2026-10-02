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
