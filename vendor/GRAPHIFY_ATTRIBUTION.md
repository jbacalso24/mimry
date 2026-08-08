# Historical Graphify attribution

Graphify is no longer MIMRY's core engine, runtime dependency, or git submodule.
MIMRY now builds graph artifacts with the native engine in `src/mimry/core/`.

The project previously evaluated and integrated upstream Graphify under its MIT
license. This attribution and the frozen benchmark baseline are retained so that
the historical comparison remains auditable:

- Repository: https://github.com/safishamsi/graphify
- Default branch inspected: `v8`
- Pinned commit evaluated: `44c0a5e33c7011813dcebf1a8850c1c6005bf500`
- Python package: `graphifyy`
- License: MIT
- Copyright: Copyright (c) 2026 Safi Shamsi
- Frozen result: `benchmarks/baseline.graphify.json`

See `THIRD_PARTY.md` for the current dependency and attribution boundary.