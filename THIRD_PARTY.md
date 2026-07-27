# Third-party notices

## Graphify

MIMRY treats Graphify as core graph infrastructure while keeping MIMRY's product boundary around CLI, schema, storage, ranking, and context packs.

- Upstream repository: https://github.com/safishamsi/graphify
- Default branch inspected: `v8`
- Pinned commit: `44c0a5e33c7011813dcebf1a8850c1c6005bf500`
- Dependency policy: PEP 508 Git URL pinned to that commit in internal direct-install MIMRY artifact metadata
- Python package: `graphifyy`
- CLI: `graphify`
- MCP CLI: `graphify-mcp`
- License: MIT
- Copyright: Copyright (c) 2026 Safi Shamsi

The upstream source is tracked as an optional git submodule at `vendor/graphify` instead of a copied tree so the MIMRY repo stays light while retaining a pinned, inspectable Graphify baseline. Normal direct installations use the same commit pin from package metadata; the submodule is not included in release artifacts. Because the metadata contains this direct Git reference, this internal release must not be published to PyPI or a PyPI-style index.

Rules for MIMRY integration:

1. Do not expose Graphify's installer, hook, clone, provider config, or assistant integration commands by default.
2. Default to local/no-network Graphify paths only.
3. Keep Graphify outputs under MIMRY-owned state/cache paths, not raw `graphify-out/`, unless explicitly debugging upstream.
4. Preserve upstream MIT license and this notice when updating the submodule.
