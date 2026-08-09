from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mimry.benchmark import PRIVACY_CANARY, _run, evaluate, load_cases, ndcg_at_k, recall_at_k, token_proxy

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "benchmarks" / "fixtures" / "agent_repo"


def test_metric_contract():
    grades = {"primary.py": 3, "helper.py": 2}
    assert ndcg_at_k(["primary.py", "helper.py"], grades) == 1.0
    assert ndcg_at_k(["noise.py"], grades) == 0.0
    assert recall_at_k(["primary.py"], grades) == 0.5
    assert token_proxy("é") == 1


def test_manifest_rejects_path_escape_and_missing_primary(tmp_path):
    manifest = tmp_path / "bad.json"
    manifest.write_text(
        json.dumps({"schema_version": 1, "cases": [{"id": "bad", "query": "x", "grades": {"../x": 2}}]})
    )
    with pytest.raises(ValueError):
        load_cases(manifest, FIXTURE)


def test_frozen_manifest_is_valid_and_gold_is_outside_fixture():
    cases = load_cases(ROOT / "benchmarks" / "cases.v1.json", FIXTURE)
    # Assert the invariant, not a magic count: every case in the manifest loads,
    # ids are unique, and the gold answers never live inside the fixture the
    # benchmark indexes.
    assert cases
    assert len({case["id"] for case in cases}) == len(cases)
    assert not (FIXTURE / "cases.v1.json").exists()


def test_end_to_end_report_is_schema_versioned_and_isolated():
    report = evaluate(ROOT / "benchmarks" / "cases.v1.json", FIXTURE, repeat=1, gate_latency=False)
    assert report["schema_version"] == 1
    assert report["profile"] == "quick-local-index"
    assert len(report["cases"]) == len(load_cases(ROOT / "benchmarks" / "cases.v1.json", FIXTURE))
    assert "MIMRY_BENCHMARK_CANARY" not in json.dumps(report)
    assert report["graph_enabled"] is True
    assert report["graph_nodes"] > 0
    assert report["graph_edges"] > 0
    # Core retrieval quality checks must pass (latency gates disabled for this ad-hoc run)
    assert report["checks"]["ndcg_at_5"]
    assert report["checks"]["recall_at_5"]
    assert report["checks"]["primary_hit_at_3"]
    assert report["checks"]["decoy_rate_at_5"]
    assert report["checks"]["abstention_accuracy"]


def test_failed_command_redacts_canary_before_raising(monkeypatch, tmp_path):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", f"failed {PRIVACY_CANARY}"),
    )
    with pytest.raises(RuntimeError) as error:
        _run(tmp_path, {}, "init", "--skip-graph")
    assert PRIVACY_CANARY not in str(error.value)
    assert "privacy canary detected" in str(error.value)


def test_benchmark_sandbox_can_parse_every_supported_language(tmp_path):
    """The sandbox must not silently degrade the index it measures.

    The env allowlist scrubs the Windows variables tree-sitter-language-pack
    needs to resolve its grammar cache. When that fails it raises, MIMRY
    records `parse_error:RuntimeError`, and indexing continues -- so every
    TypeScript file lost its symbols while the benchmark still produced a
    plausible-looking score. A whole language degrading to zero symbols must
    fail this test, not quietly lower the measurement.
    """
    import shutil

    from mimry.benchmark import _env, _run

    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    env = _env(tmp_path)
    _run(repo, env, "init", "--skip-graph")
    _run(repo, env, "index")

    pointer = json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
    index = Path(pointer["indexPath"])
    files = [json.loads(line) for line in (index / "files.jsonl").read_text(encoding="utf-8").splitlines() if line]

    broken = sorted(f["rel_path"] for f in files if str(f.get("parse_status", "")).startswith("parse_error"))
    assert broken == [], f"benchmark sandbox failed to parse: {broken}"

    symbols = [json.loads(line) for line in (index / "symbols.jsonl").read_text(encoding="utf-8").splitlines() if line]
    by_id = {f["file_id"]: f["rel_path"] for f in files}
    defining = {by_id[s["file_id"]] for s in symbols if s["file_id"] in by_id}
    # One representative per parsed language in the fixture.
    for expected in ("web/src/lib/payments.ts", "backend/api/auth.py", "db/schema.sql"):
        assert expected in defining, f"{expected} produced no symbols in the benchmark sandbox"
