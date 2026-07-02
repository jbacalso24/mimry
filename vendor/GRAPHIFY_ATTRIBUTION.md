# Graphify attribution

MIMRY treats Graphify as core graph infrastructure.

Upstream Graphify is tracked as a pinned git submodule at `vendor/graphify` instead of a directly copied tree.

- Repository: https://github.com/safishamsi/graphify
- Default branch inspected: `v8`
- Pinned commit: `44c0a5e33c7011813dcebf1a8850c1c6005bf500`
- Python package: `graphifyy`
- CLI: `graphify`
- MCP CLI: `graphify-mcp`
- License: MIT
- Copyright: Copyright (c) 2026 Safi Shamsi

MIMRY exposes its stable graph boundary through `src/mimry/graphify_core`.
See `THIRD_PARTY.md` for integration rules.
