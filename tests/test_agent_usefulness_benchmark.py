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
