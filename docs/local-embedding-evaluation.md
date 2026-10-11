# Local embedding evaluation harness

This is an **opt-in research harness**, not a MIMRY runtime backend.
It leaves default indexing and retrieval unchanged and adds no dependency.
It compares a freshly indexed lexical+graph baseline with pure local embeddings and a frozen hybrid on the shipped public fixture only.

```bash
uv run python scripts/local_embedding_evaluation.py \
  --embedding-provider ollama \
  --endpoint http://127.0.0.1:11434 \
  --model nomic-embed-text:latest \
  --expected-dimension 768 \
  --timeout 30 \
  --output /tmp/mimry-local-embedding-report.json
```

The command first checks that the exact model is already installed.
It never downloads a model.
The endpoint must be an explicit `http://127.0.0.1:<port>` origin; hostnames, other loopback addresses, HTTPS, credentials, paths, redirects, and proxy discovery are rejected.
Requests and responses are bounded, timeouts are mandatory, and every vector must have the expected dimension and finite, numeric, non-zero values.

## Frozen comparison

Before the real run, the comparison was fixed as follows:

- baseline: a fresh MIMRY index and lexical+graph `find` results;
- embedding: cosine-ranked fixture files from the exact installed model;
- hybrid: reciprocal-rank fusion with `k=60`, equal `1:1` rank contributions, the top 20 candidates from each arm, and repository-relative path order for ties;
- warm latency: one unscored warm-up and five measured queries per case;
- document input: `search_document: <relative path>\n<fixture text>`;
- query input: `search_query: <frozen query>`.

Only `benchmarks/fixtures/agent_repo` is embedded.
Gold grades remain in `benchmarks/cases.v1.json`, outside the fixture, and are never part of embedding inputs.
The schema-v2 report records source SHA, fixture/case/input hashes, model digest, Ollama version, every retained candidate ranking (up to the frozen 20-per-arm limit), embedding cosine scores, hybrid RRF scores, quality metrics, and observed latency.
Its `fixture_sha256` is computed the same way as in `benchmarks/result.core.json`.
The full retained rankings and scores are fusion-audit inputs; `quality_positive_cases_only.metric_cutoffs` separately identifies the at-5 (and primary-hit-at-3) metric windows.
`source_sha` is the checked-out Git commit, while `harness_sha256` binds the exact script bytes used, including uncommitted research-harness changes.

## Claim boundary

The synthetic fixture has already been inspected and measured, so this is replay and smoke evidence, not an untouched holdout.
It does not support claims about universal quality, private repositories, product adoption, peak memory, or vector-index storage.
No abstention metric is claimed because no similarity threshold was preregistered.
Synthetic-vector unit tests validate error handling and ranking mechanics only; model evidence must come from an actual local run and a separately retained report.
