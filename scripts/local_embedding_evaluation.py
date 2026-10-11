#!/usr/bin/env python3
"""Compare MIMRY retrieval with local embeddings on its fixture."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import math
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from statistics import median
from typing import Any
from urllib.parse import urlsplit

from mimry.benchmark import (
    _digest,
    _env,
    _paths,
    _run,
    load_cases,
    ndcg_at_k,
    percentile,
    recall_at_k,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "benchmarks" / "fixtures" / "agent_repo"
CASES = ROOT / "benchmarks" / "cases.v1.json"
RRF_K = 60
CANDIDATE_LIMIT = 20
MAX_INPUT_BYTES = 1_048_576
MAX_ITEM_BYTES = 65_536
MAX_RESPONSE_BYTES = 10_485_760
CLAIM_BOUNDARY = (
    "fixture replay already seen during development; local smoke evidence only, not an untouched "
    "holdout, product decision, or universal performance claim"
)


def validate_endpoint(value: str) -> tuple[str, int]:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("endpoint must be exactly an http://127.0.0.1 loopback origin")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("endpoint has an invalid port") from error
    if port is None or not 1 <= port <= 65535:
        raise ValueError("endpoint must include a valid explicit port")
    return parsed.hostname, port


class OllamaClient:
    """Direct-loopback client; no redirects, proxies, or downloads."""

    def __init__(self, endpoint: str, *, timeout: float, expected_dimension: int) -> None:
        self.host, self.port = validate_endpoint(endpoint)
        if not 0 < timeout <= 120:
            raise ValueError("timeout must be in (0, 120] seconds")
        if not 1 <= expected_dimension <= 65_536:
            raise ValueError("expected dimension must be between 1 and 65536")
        self.timeout = timeout
        self.expected_dimension = expected_dimension

    def _json(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        if body is not None and len(body) > MAX_INPUT_BYTES:
            raise ValueError("request exceeds the 1 MiB harness limit")
        connection = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            connection.request(
                method, path, body=body, headers={"Content-Type": "application/json"}
            )
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise RuntimeError("redirects are forbidden")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise RuntimeError("response exceeds the 10 MiB harness limit")
            if response.status != 200:
                raise RuntimeError(f"local provider returned HTTP {response.status}")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise RuntimeError("provider response must be a JSON object")
            return value
        finally:
            connection.close()

    def readiness(self, model: str) -> dict[str, str]:
        if not model or len(model) > 200:
            raise ValueError("model name is required and bounded")
        tags = self._json("GET", "/api/tags")
        models = tags.get("models")
        if not isinstance(models, list):
            raise RuntimeError("provider tags response has no models list")
        match = next(
            (item for item in models if isinstance(item, dict) and item.get("name") == model), None
        )
        if match is None:
            raise RuntimeError(f"required local model is not installed: {model}")
        version = self._json("GET", "/api/version").get("version")
        digest = match.get("digest")
        if not isinstance(digest, str) or not digest:
            raise RuntimeError("installed model has no digest")
        return {"model_digest": digest, "runtime_version": str(version or "unknown")}

    def embed(
        self, model: str, inputs: list[str], *, keep_alive: str = "5m"
    ) -> tuple[list[list[float]], float]:
        if not inputs or any(len(value.encode()) > MAX_ITEM_BYTES for value in inputs):
            raise ValueError("embedding inputs must be non-empty and at most 64 KiB each")
        started = time.perf_counter_ns()
        payload = self._json(
            "POST", "/api/embed", {"model": model, "input": inputs, "keep_alive": keep_alive}
        )
        elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
        raw_vectors = payload.get("embeddings")
        if not isinstance(raw_vectors, list) or len(raw_vectors) != len(inputs):
            raise RuntimeError("provider returned the wrong vector count")
        vectors: list[list[float]] = []
        for raw in raw_vectors:
            if not isinstance(raw, list) or len(raw) != self.expected_dimension:
                raise RuntimeError("provider returned an unexpected vector dimension")
            if not all(
                isinstance(value, (int, float)) and not isinstance(value, bool) for value in raw
            ):
                raise RuntimeError("provider returned a non-numeric vector")
            vector = [float(value) for value in raw]
            if not all(math.isfinite(value) for value in vector):
                raise RuntimeError("provider returned a non-finite vector")
            if math.sqrt(sum(value * value for value in vector)) == 0:
                raise RuntimeError("provider returned a zero vector")
            vectors.append(vector)
        return vectors, elapsed_ms


def cosine(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    denominator = math.sqrt(sum(a * a for a in left) * sum(b * b for b in right))
    return numerator / denominator if denominator else 0.0


def rank_vectors(query: list[float], documents: dict[str, list[float]]) -> list[str]:
    return [path for path, _ in rank_vector_scores(query, documents)]


def rank_vector_scores(
    query: list[float], documents: dict[str, list[float]]
) -> list[tuple[str, float]]:
    return sorted(
        ((path, cosine(query, vector)) for path, vector in documents.items()),
        key=lambda item: (-item[1], item[0]),
    )


def reciprocal_rank_fusion(baseline: list[str], embedding: list[str]) -> list[str]:
    return [path for path, _ in reciprocal_rank_fusion_scores(baseline, embedding)]


def reciprocal_rank_fusion_scores(
    baseline: list[str], embedding: list[str]
) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranking in (baseline[:CANDIDATE_LIMIT], embedding[:CANDIDATE_LIMIT]):
        for rank, path in enumerate(ranking, 1):
            scores[path] = scores.get(path, 0.0) + 1.0 / (RRF_K + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def report_case(
    case: dict[str, Any],
    baseline: list[str],
    embedding_scores: list[tuple[str, float]],
) -> dict[str, Any]:
    embedding = [path for path, _ in embedding_scores]
    hybrid_scores = reciprocal_rank_fusion_scores(baseline, embedding)
    return {
        "id": case["id"],
        "query": case["query"],
        "expect_empty": bool(case.get("expect_empty")),
        "baseline_ranking": [
            {"path": path, "rank": rank} for rank, path in enumerate(baseline, 1)
        ],
        "embedding_ranking": [
            {"path": path, "rank": rank, "cosine_score": score}
            for rank, (path, score) in enumerate(embedding_scores, 1)
        ],
        "hybrid_ranking": [
            {"path": path, "rank": rank, "rrf_score": score}
            for rank, (path, score) in enumerate(hybrid_scores, 1)
        ],
    }


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _metrics(cases: list[dict[str, Any]], rankings: dict[str, list[str]]) -> dict[str, float]:
    positive = [case for case in cases if not case.get("expect_empty")]
    rows = [(case, rankings[case["id"]]) for case in positive]
    slots = sum(min(5, len(paths)) for _, paths in rows)
    return {
        "ndcg_at_5": sum(ndcg_at_k(paths, case["grades"]) for case, paths in rows) / len(rows),
        "recall_at_5": sum(recall_at_k(paths, case["grades"]) for case, paths in rows) / len(rows),
        "primary_hit_at_3": sum(
            any(case["grades"].get(path) == 3 for path in paths[:3]) for case, paths in rows
        )
        / len(rows),
        "decoy_rate_at_5": sum(
            len(set(case.get("decoys", [])).intersection(paths[:5])) for case, paths in rows
        )
        / max(1, slots),
    }


def evaluate(model: str, endpoint: str, expected_dimension: int, timeout: float) -> dict[str, Any]:
    cases = load_cases(CASES, FIXTURE)
    client = OllamaClient(endpoint, timeout=timeout, expected_dimension=expected_dimension)
    provenance = client.readiness(model)
    files = sorted(path for path in FIXTURE.rglob("*") if path.is_file())
    document_inputs = []
    for path in files:
        relative = path.relative_to(FIXTURE).as_posix()
        document_inputs.append(f"search_document: {relative}\n{path.read_text(encoding='utf-8')}")
    query_inputs = [f"search_query: {case['query']}" for case in cases]
    input_hashes = {
        "documents_sha256": _sha256_json(document_inputs),
        "queries_sha256": _sha256_json(query_inputs),
    }

    with tempfile.TemporaryDirectory(prefix="mimry-local-embedding-") as raw:
        sandbox, repo = Path(raw), Path(raw) / "repo"
        shutil.copytree(FIXTURE, repo)
        env = _env(sandbox)
        _run(repo, env, "init", "--skip-graph")
        _run(repo, env, "index", timeout=300)
        baseline: dict[str, list[str]] = {}
        baseline_latencies: list[float] = []
        for case in cases:
            samples: list[float] = []
            paths: list[str] = []
            for _ in range(6):
                stdout, _, elapsed = _run(
                    repo, env, "find", case["query"], "--limit", str(CANDIDATE_LIMIT)
                )
                paths, samples = _paths(stdout), [*samples, elapsed]
            baseline[case["id"]] = paths
            baseline_latencies.extend(samples[1:])

    document_vectors, cold_document_ms = client.embed(model, document_inputs)
    documents = {
        path.relative_to(FIXTURE).as_posix(): vector
        for path, vector in zip(files, document_vectors, strict=True)
    }
    embedding: dict[str, list[str]] = {}
    embedding_scores: dict[str, list[tuple[str, float]]] = {}
    embedding_latencies: list[float] = []
    for case, text in zip(cases, query_inputs, strict=True):
        client.embed(model, [text])  # unscored warm-up
        vectors = []
        for _ in range(5):
            result, elapsed = client.embed(model, [text])
            vectors.append(result[0])
            embedding_latencies.append(elapsed)
        scored_ranks = [rank_vector_scores(vector, documents) for vector in vectors]
        ranks = [[path for path, _ in ranking] for ranking in scored_ranks]
        if any(ranking != ranks[0] for ranking in ranks[1:]):
            raise RuntimeError("embedding ranking was not deterministic across warm repeats")
        embedding[case["id"]] = ranks[0]
        embedding_scores[case["id"]] = scored_ranks[0]
    client.embed(model, [query_inputs[0]], keep_alive="0s")
    hybrid = {
        case["id"]: reciprocal_rank_fusion(baseline[case["id"]], embedding[case["id"]])
        for case in cases
    }
    source_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, text=True, capture_output=True
    ).stdout.strip()
    rows = [
        report_case(case, baseline[case["id"]], embedding_scores[case["id"]]) for case in cases
    ]
    return {
        "schema_version": 2,
        "profile": "opt-in-local-embedding-fixture-replay",
        "claim_boundary": CLAIM_BOUNDARY,
        "source_sha": source_sha,
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        # Same digest as benchmarks/result.core.json, so reports can be
        # matched to the fixture the committed evidence used.
        "fixture_sha256": _digest(FIXTURE),
        "cases_sha256": hashlib.sha256(CASES.read_bytes()).hexdigest(),
        "input_hashes": input_hashes,
        "provider": {
            "type": "ollama-loopback",
            "endpoint": endpoint,
            "model": model,
            "expected_dimension": expected_dimension,
            **provenance,
        },
        "frozen_fusion": {
            "method": "reciprocal-rank-fusion",
            "k": RRF_K,
            "weights": {"baseline": 1, "embedding": 1},
            "candidate_limit_per_arm": CANDIDATE_LIMIT,
            "tie_break": "stable ascending repository-relative path",
        },
        "gold_labels_embedded": False,
        "document_count": len(document_inputs),
        "query_count": len(query_inputs),
        "latency_ms": {
            "cold_document_batch": cold_document_ms,
            "baseline_query_median": median(baseline_latencies),
            "baseline_query_p95": percentile(baseline_latencies, 0.95),
            "embedding_query_median": median(embedding_latencies),
            "embedding_query_p95": percentile(embedding_latencies, 0.95),
        },
        "quality_positive_cases_only": {
            "metric_cutoffs": {
                "ndcg": 5,
                "recall": 5,
                "primary_hit": 3,
                "decoy_rate": 5,
            },
            "baseline": _metrics(cases, baseline),
            "embedding": _metrics(cases, embedding),
            "hybrid": _metrics(cases, hybrid),
        },
        "abstention": {
            "evaluated": False,
            "reason": (
                "no embedding similarity threshold was preregistered; embedding and "
                "hybrid arms always rank candidates"
            ),
        },
        "cases": rows,
        "limitations": [
            (
                "The shipped synthetic fixture has already been observed and is not an "
                "untouched holdout."
            ),
            (
                "This smoke run cannot justify product adoption or generalize to private "
                "repositories."
            ),
            (
                "Latency includes localhost HTTP and model execution but does not measure "
                "peak RSS or index storage."
            ),
            (
                "The baseline is freshly indexed and queried in the same run; no committed "
                "baseline metrics are reused."
            ),
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--embedding-provider", choices=["ollama"], required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--expected-dimension", type=int, required=True)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(args.model, args.endpoint, args.expected_dimension, args.timeout)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
