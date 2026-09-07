# MIMRY agent retrieval benchmark

This frozen synthetic corpus measures a **retrieval-usefulness proxy**: ranked-file relevance, agent-facing output size, and public CLI latency. It is not proof of agent task completion, developer-time savings, or exact model-token savings.

Run from a source checkout or extracted sdist: `uv run python scripts/agent_usefulness_benchmark.py --repeat 3 --graph`. The native engine builds graph artifacts during indexing, so reports truthfully set `graph_enabled` and include graph node/edge counts even if an older caller omits `--graph`; the flag remains in the documented command to make the measured mode explicit. The runner uses a fresh temporary HOME/cache, adds a fake-secret canary, indexes only the fixture (never the gold labels), scans command streams plus generated repo/cache files for leakage, and emits a schema-versioned case-level JSON report. The benchmark is intentionally not an installed wheel entry point because its frozen corpus is source-distribution test data.

## Frozen v1 floors

- macro nDCG@5 (grades 1-3) >= 0.70; macro Recall@5 (grades 2-3) >= 0.75; primary grade-3 hit@3 >= 0.80
- micro-averaged decoy rate over returned positive-case top-five slots <= 0.15; explicit no-match abstention accuracy = 1.00
- context output <= 2,500 token-proxy units, where proxy = ceil(UTF-8 bytes / 4) and is **not** an exact tokenizer
- Linux/Python 3.11 dedicated gate: find p95 <= 1,000 ms; context p95 <= 1,500 ms

Threshold changes require a schema/version bump and a before/after case report. The harness exits nonzero until every floor passes. The dedicated Ubuntu gate validates the committed report before running the benchmark fresh; fresh evidence is uploaded from `/tmp/artifact` and never overwrites the tracked report. Other support-matrix jobs do not gate noisy timing.

The committed native-graph report is a PASS whose provenance binds the portable fixture
bytes, canonical cases manifest, retrieval implementation, and exact frozen thresholds.
The historical no-graph control measurement remains attributed in
`baseline.nograph.json`; it is a comparison, not a current-run claim.
