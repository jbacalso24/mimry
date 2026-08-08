"""Deterministic retrieval-usefulness benchmark for MIMRY agent workflows."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from statistics import median
from typing import Any

SCHEMA_VERSION = 1
PRIVACY_CANARY = "MIMRY_BENCHMARK_CANARY_7f8e9d"
DEFAULT_THRESHOLDS = {
    "ndcg_at_5_min": 0.70,
    "recall_at_5_min": 0.75,
    "primary_hit_at_3_min": 0.80,
    "decoy_rate_at_5_max": 0.15,
    "abstention_accuracy_min": 1.0,
    "find_p95_ms_max": 1000.0,
    "context_p95_ms_max": 1500.0,
    "context_token_proxy_max": 2500,
}
_ENV_ALLOWLIST = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "SYSTEMROOT")
_RESULT_RE = re.compile(r"^\d+\. (.+)$")
_GRAPH_SIZE_RE = re.compile(r"graph\.json:\s*yes\s*\((\d+)\s*nodes/(\d+)\s*edges")


def token_proxy(text: str) -> int:
    """Return an explicitly approximate token count: ceil(UTF-8 bytes / 4)."""
    return math.ceil(len(text.encode("utf-8")) / 4)


def ndcg_at_k(paths: list[str], grades: dict[str, int], k: int = 5) -> float:
    gains = [grades.get(path, 0) for path in paths[:k]]
    dcg = sum((2**grade - 1) / math.log2(rank + 2) for rank, grade in enumerate(gains))
    ideal = sorted(grades.values(), reverse=True)[:k]
    idcg = sum((2**grade - 1) / math.log2(rank + 2) for rank, grade in enumerate(ideal))
    return dcg / idcg if idcg else 1.0


def recall_at_k(paths: list[str], grades: dict[str, int], k: int = 5) -> float:
    relevant = {path for path, grade in grades.items() if grade >= 2}
    return len(relevant.intersection(paths[:k])) / len(relevant) if relevant else 1.0


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1)] if ordered else 0.0


def load_cases(path: Path, fixture: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if payload.get("schema_version") != SCHEMA_VERSION or not isinstance(cases, list) or not cases:
        raise ValueError("benchmark manifest must contain schema_version 1 and non-empty cases")
    fixture_files = {p.relative_to(fixture).as_posix() for p in fixture.rglob("*") if p.is_file()}
    seen: set[str] = set()
    for case in cases:
        case_id, grades = case.get("id"), case.get("grades")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError("case IDs must be unique non-empty strings")
        seen.add(case_id)
        if not isinstance(case.get("query"), str) or not case["query"].strip() or not isinstance(grades, dict):
            raise ValueError(f"case {case_id} has an invalid query or grades")
        if case.get("expect_empty"):
            if grades:
                raise ValueError(f"abstention case {case_id} cannot have grades")
        elif 3 not in grades.values():
            raise ValueError(f"positive case {case_id} requires a grade-3 primary file")
        for rel, grade in grades.items():
            candidate = Path(rel)
            if candidate.is_absolute() or ".." in candidate.parts or grade not in {1, 2, 3} or rel not in fixture_files:
                raise ValueError(f"case {case_id} has unsafe/missing path or invalid grade: {rel}")
        for rel in case.get("decoys", []):
            if rel not in fixture_files:
                raise ValueError(f"case {case_id} references missing decoy: {rel}")
    return cases


def _env(sandbox: Path) -> dict[str, str]:
    env = {name: os.environ[name] for name in _ENV_ALLOWLIST if os.environ.get(name)}
    env.update(
        {
            "HOME": str(sandbox / "home"),
            # Windows resolves Path.home() from USERPROFILE, not HOME. Without it the
            # sandbox has no resolvable home and safe_root() raises. Point it at the
            # sandbox so isolation holds and the guard stays strict.
            "USERPROFILE": str(sandbox / "home"),
            "XDG_CACHE_HOME": str(sandbox / "xdg-cache"),
            "XDG_CONFIG_HOME": str(sandbox / "xdg-config"),
            "MIMRY_CACHE_HOME": str(sandbox / "mimry-cache"),
            "TMPDIR": str(sandbox / "tmp"),
            "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
            "PYTHONNOUSERSITE": "1",
            "NO_COLOR": "1",
        }
    )
    for key in ("HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "MIMRY_CACHE_HOME", "TMPDIR"):
        Path(env[key]).mkdir(parents=True, exist_ok=True)
    return env


def _run(repo: Path, env: dict[str, str], *args: str, timeout: int = 5) -> tuple[str, str, float]:
    started = time.perf_counter_ns()
    result = subprocess.run(
        [sys.executable, "-m", "mimry.cli", "--root", str(repo), *args],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        if PRIVACY_CANARY in detail:
            detail = "[REDACTED: privacy canary detected in failed command output]"
        raise RuntimeError(f"mimry {' '.join(args)} failed ({result.returncode}): {detail}")
    return result.stdout, result.stderr, elapsed_ms


def _assert_canary_absent(sandbox: Path, planted_file: Path, canary: str, streams: list[str]) -> None:
    marker = canary.encode("utf-8")
    if any(canary in stream for stream in streams):
        raise RuntimeError("privacy canary leaked into benchmark command output")
    for path in sandbox.rglob("*"):
        if not path.is_file() or path == planted_file:
            continue
        try:
            leaked = marker in path.read_bytes()
        except OSError:
            continue
        if leaked:
            raise RuntimeError(f"privacy canary leaked into benchmark persistence: {path.relative_to(sandbox)}")


def _paths(stdout: str) -> list[str]:
    return [match.group(1).strip() for line in stdout.splitlines() if (match := _RESULT_RE.match(line))]


def _graph_size(status_text: str) -> tuple[int, int]:
    """Parse graph node and edge counts from mimry status output."""
    match = _GRAPH_SIZE_RE.search(status_text)
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def _digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def evaluate(
    cases_path: Path,
    fixture: Path,
    *,
    repeat: int = 3,
    gate_latency: bool = True,
    graph: bool = False,
    engine: str = "core",
) -> dict[str, Any]:
    if not 1 <= repeat <= 10:
        raise ValueError("repeat must be between 1 and 10")
    cases = load_cases(cases_path, fixture)
    with tempfile.TemporaryDirectory(prefix="mimry-agent-benchmark-") as raw:
        sandbox, canary = Path(raw), PRIVACY_CANARY
        repo = sandbox / "repo"
        shutil.copytree(fixture, repo)
        planted_file = repo / ".env"
        planted_file.write_text(f"PROVIDER_TOKEN={canary}\n", encoding="utf-8")
        env = _env(sandbox)
        graph_build_ms = 0.0
        graph_build_out = ""
        graph_build_err = ""
        graph_status_out = ""
        graph_status_err = ""
        graph_nodes, graph_edges = 0, 0
        init_out, init_err, init_ms = _run(repo, env, "init", "--skip-graph")
        index_out, index_err, index_ms = _run(repo, env, "index")
        # The native engine builds the graph inside `index`.
        if graph:
            graph_status_out, graph_status_err, _ = _run(repo, env, "status", timeout=60)
            graph_nodes, graph_edges = _graph_size(graph_status_out)
        case_reports, find_latencies, context_latencies, all_output = [], [], [], []
        all_output.extend(
            (
                init_out,
                init_err,
                index_out,
                index_err,
                graph_build_out,
                graph_build_err,
                graph_status_out,
                graph_status_err,
            )
        )
        for case in cases:
            samples, paths, find_output = [], [], ""
            for _ in range(repeat + 1):
                find_output, find_err, elapsed = _run(repo, env, "find", case["query"], "--limit", "5")
                paths, samples = _paths(find_output), [*samples, elapsed]
                all_output.extend((find_output, find_err))
            find_case = samples[1:]
            find_latencies.extend(find_case)
            context_out, context_err, context_ms = _run(repo, env, "context", case["query"])
            context_text = (repo / ".mimry" / "mimry-out" / "context" / "latest.md").read_text(encoding="utf-8")
            context_latencies.append(context_ms)
            all_output.extend((context_out, context_err, context_text))
            grades, decoys = case["grades"], set(case.get("decoys", []))
            case_reports.append(
                {
                    "id": case["id"],
                    "query": case["query"],
                    "paths": paths,
                    "ndcg_at_5": ndcg_at_k(paths, grades),
                    "recall_at_5": recall_at_k(paths, grades),
                    "primary_hit_at_3": any(grades.get(path) == 3 for path in paths[:3]),
                    "decoys_at_5": len(decoys.intersection(paths[:5])),
                    "expect_empty": case.get("expect_empty", False),
                    "abstained": (not paths) if case.get("expect_empty") else None,
                    "find_median_ms": median(find_case),
                    "find_token_proxy": token_proxy(find_output),
                    "context_ms": context_ms,
                    "context_token_proxy": token_proxy(context_text),
                }
            )
        _assert_canary_absent(sandbox, planted_file, canary, all_output)
    positive = [case for case in case_reports if not case["expect_empty"]]
    abstention = [case for case in case_reports if case["expect_empty"]]
    retrieved_slots = sum(min(5, len(case["paths"])) for case in positive)
    aggregates = {
        "ndcg_at_5": sum(c["ndcg_at_5"] for c in positive) / len(positive),
        "recall_at_5": sum(c["recall_at_5"] for c in positive) / len(positive),
        "primary_hit_at_3": sum(c["primary_hit_at_3"] for c in positive) / len(positive),
        "decoy_rate_at_5": sum(c["decoys_at_5"] for c in positive) / max(1, retrieved_slots),
        "abstention_accuracy": sum(bool(c["abstained"]) for c in abstention) / max(1, len(abstention)),
        "find_p95_ms": percentile(find_latencies, 0.95),
        "context_p95_ms": percentile(context_latencies, 0.95),
        "context_token_proxy_max": max(c["context_token_proxy"] for c in case_reports),
    }
    checks = {
        "ndcg_at_5": aggregates["ndcg_at_5"] >= DEFAULT_THRESHOLDS["ndcg_at_5_min"],
        "recall_at_5": aggregates["recall_at_5"] >= DEFAULT_THRESHOLDS["recall_at_5_min"],
        "primary_hit_at_3": aggregates["primary_hit_at_3"] >= DEFAULT_THRESHOLDS["primary_hit_at_3_min"],
        "decoy_rate_at_5": aggregates["decoy_rate_at_5"] <= DEFAULT_THRESHOLDS["decoy_rate_at_5_max"],
        "abstention_accuracy": aggregates["abstention_accuracy"] >= DEFAULT_THRESHOLDS["abstention_accuracy_min"],
        "context_token_proxy_max": aggregates["context_token_proxy_max"]
        <= DEFAULT_THRESHOLDS["context_token_proxy_max"],
    }
    if gate_latency:
        checks.update(
            {
                "find_p95_ms": aggregates["find_p95_ms"] <= DEFAULT_THRESHOLDS["find_p95_ms_max"],
                "context_p95_ms": aggregates["context_p95_ms"] <= DEFAULT_THRESHOLDS["context_p95_ms_max"],
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "profile": "quick-local-index",
        "claim_boundary": "retrieval usefulness proxy; not agent task completion or model-token savings",
        "fixture_sha256": _digest(fixture),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "repeat": repeat,
        "setup_ms": {"init": init_ms, "index": index_ms},
        "thresholds": DEFAULT_THRESHOLDS,
        "aggregates": aggregates,
        "checks": checks,
        "state": "PASS" if all(checks.values()) else "FAIL",
        "cases": case_reports,
        "engine": engine,
        "graph_enabled": graph,
        "graph_build_ms": graph_build_ms,
        # Total wall clock to a queryable index WITH a graph. The native engine folds
        # graph construction into indexing, so comparing graph_build_ms alone would
        # flatter it; this counts everything either engine needs.
        "setup_total_ms": init_ms + index_ms + graph_build_ms,
        "graph_nodes": graph_nodes,
        "graph_edges": graph_edges,
    }


COMPARE_METRICS = ("ndcg_at_5", "recall_at_5", "primary_hit_at_3")


def compare(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """Compare a run against a recorded baseline. Higher is better for COMPARE_METRICS;
    lower is better for decoy_rate_at_5."""
    result = {
        "regressed": False,
        "baseline_engine": baseline.get("engine"),
        "baseline_graph_build_ms": baseline.get("graph_build_ms"),
        "baseline_graph_nodes": baseline.get("graph_nodes"),
        "baseline_graph_edges": baseline.get("graph_edges"),
        "current_engine": current.get("engine"),
        "current_graph_build_ms": current.get("graph_build_ms"),
        "current_graph_nodes": current.get("graph_nodes"),
        "current_graph_edges": current.get("graph_edges"),
        "metrics": {},
    }
    tolerance = 1e-9
    for metric in COMPARE_METRICS:
        baseline_val = baseline.get("aggregates", {}).get(metric, 0.0)
        current_val = current.get("aggregates", {}).get(metric, 0.0)
        delta = current_val - baseline_val
        metric_regressed = (current_val + tolerance) < baseline_val
        result["metrics"][metric] = {
            "baseline": baseline_val,
            "current": current_val,
            "delta": delta,
            "regressed": metric_regressed,
        }
        if metric_regressed:
            result["regressed"] = True
    baseline_decoy = baseline.get("aggregates", {}).get("decoy_rate_at_5", 1.0)
    current_decoy = current.get("aggregates", {}).get("decoy_rate_at_5", 1.0)
    delta_decoy = current_decoy - baseline_decoy
    decoy_regressed = (current_decoy - tolerance) > baseline_decoy
    result["metrics"]["decoy_rate_at_5"] = {
        "baseline": baseline_decoy,
        "current": current_decoy,
        "delta": delta_decoy,
        "regressed": decoy_regressed,
    }
    if decoy_regressed:
        result["regressed"] = True
    return result


def main(argv: list[str] | None = None) -> int:
    project = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Run MIMRY's deterministic agent retrieval benchmark.")
    parser.add_argument("--cases", type=Path, default=project / "benchmarks" / "cases.v1.json")
    parser.add_argument("--fixture", type=Path, default=project / "benchmarks" / "fixtures" / "agent_repo")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--no-latency-gate", action="store_true")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--out", type=Path, help="Alias for --json-output")
    parser.add_argument("--graph", action="store_true", help="Enable graph mode")
    parser.add_argument("--engine", type=str, default="core", help="Engine label for the report")
    parser.add_argument("--compare", type=Path, help="Path to baseline JSON for comparison")
    args = parser.parse_args(argv)
    output_path = args.out or args.json_output
    try:
        report = evaluate(
            args.cases.resolve(),
            args.fixture.resolve(),
            repeat=args.repeat,
            gate_latency=not args.no_latency_gate,
            graph=args.graph,
            engine=args.engine,
        )
        code = 0 if report["state"] == "PASS" else 1
    except Exception as exc:
        report, code = {"schema_version": SCHEMA_VERSION, "state": "FAIL", "error": str(exc)}, 2
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
    if args.compare and code != 2:
        try:
            baseline = json.loads(args.compare.read_text(encoding="utf-8"))
            comparison = compare(report, baseline)
            print("\n--- Comparison to baseline ---")
            for metric, values in comparison["metrics"].items():
                regression_marker = " [REGRESSED]" if values["regressed"] else ""
                print(
                    f"{metric}: {values['baseline']:.4f} -> {values['current']:.4f} "
                    f"(delta: {values['delta']:+.4f}){regression_marker}"
                )
            print(f"\nOverall: {'REGRESSED' if comparison['regressed'] else 'OK'}")
            if comparison["regressed"]:
                code = 1
        except Exception as exc:
            print(f"Comparison failed: {exc}", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
