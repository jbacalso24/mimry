# Third-party notices

## Graphify

MIMRY treats Graphify as core graph infrastructure while keeping MIMRY's product boundary around CLI, schema, storage, ranking, and context packs.

- Upstream repository: https://github.com/safishamsi/graphify
- Default branch inspected: `v8`
- Pinned commit: `44c0a5e33c7011813dcebf1a8850c1c6005bf500`
- Python package: `graphifyy`
- CLI: `graphify`
- MCP CLI: `graphify-mcp`
- License: MIT
- Copyright: Copyright (c) 2026 Safi Shamsi

The upstream source is tracked as a git submodule at `vendor/graphify` instead of a copied tree so the MIMRY repo stays light while retaining a pinned, inspectable Graphify baseline.

Rules for MIMRY integration:

1. Do not expose Graphify's installer, hook, clone, provider config, or assistant integration commands by default.
2. Default to local/no-network Graphify paths only.
3. Keep Graphify outputs under MIMRY-owned state/cache paths, not raw `graphify-out/`, unless explicitly debugging upstream.
4. Preserve upstream MIT license and this notice when updating the submodule.
