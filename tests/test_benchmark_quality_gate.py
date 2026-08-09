"""Quality gate for the frozen usefulness benchmark.

Asserts that the committed benchmark result represents a passing state
with locked thresholds to prevent silent degradation.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from mimry.benchmark import DEFAULT_THRESHOLDS, SCHEMA_VERSION, benchmark_input_digests


def test_benchmark_result_is_passing_and_stable():
    """Committed benchmark result must be PASS with matching thresholds."""
    project = Path(__file__).resolve().parents[1]
    result_path = project / "benchmarks" / "result.core.json"
    fixture_path = project / "benchmarks" / "fixtures" / "agent_repo"
    cases_path = project / "benchmarks" / "cases.v1.json"

    assert result_path.exists(), f"Benchmark result missing: {result_path}"
    report = json.loads(result_path.read_text(encoding="utf-8"))

    # Schema version must be current
    assert report.get("schema_version") == SCHEMA_VERSION, (
        f"Schema version mismatch: {report.get('schema_version')} != {SCHEMA_VERSION}"
    )

    # Must be in PASS state
    assert report.get("state") == "PASS", (
        f"Benchmark state is {report.get('state')}, not PASS. Thresholds: {report.get('aggregates')}"
    )

    # Evidence must be stale as soon as the fixture, gold cases, or scoring and
    # retrieval implementation changes.
    for key, current_digest in benchmark_input_digests(cases_path, fixture_path).items():
        assert report.get(key) == current_digest, f"Benchmark provenance changed for {key}"

    # Every threshold must match frozen defaults (prevent silent weakening)
    reported_thresholds = report.get("thresholds", {})
    assert reported_thresholds == DEFAULT_THRESHOLDS, "Committed thresholds differ from frozen DEFAULT_THRESHOLDS"

    # All checks must pass
    checks = report.get("checks", {})
    expected_checks = {
        key if key == "context_token_proxy_max" else key.removesuffix("_min").removesuffix("_max")
        for key in DEFAULT_THRESHOLDS
    }
    assert set(checks) == expected_checks, "Committed PASS does not contain exactly one check for every frozen floor"
    for check_name, passed in checks.items():
        assert passed, f"Check {check_name} failed in committed benchmark result"


def test_ci_validates_committed_evidence_before_writing_fresh_artifact():
    project = Path(__file__).resolve().parents[1]
    workflow = (project / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    committed_gate = workflow.index("Assert the committed report is current and PASS")
    fresh_run = workflow.index("Run the benchmark and require PASS")
    assert committed_gate < fresh_run
    assert "--out /tmp/artifact/result.core.json" in workflow
    assert "path: /tmp/artifact/result.core.json" in workflow
    assert "--out benchmarks/result.core.json" not in workflow


def test_benchmark_provenance_changes_with_fixture_cases_and_source(tmp_path):
    project = Path(__file__).resolve().parents[1]
    fixture = tmp_path / "fixture"
    cases = tmp_path / "cases.json"
    source = tmp_path / "source"
    shutil.copytree(project / "benchmarks" / "fixtures" / "agent_repo", fixture)
    shutil.copy2(project / "benchmarks" / "cases.v1.json", cases)
    shutil.copytree(project / "src" / "mimry", source)
    baseline = benchmark_input_digests(cases, fixture, source_root=source)

    (fixture / "new.txt").write_text("changed fixture\n", encoding="utf-8")
    fixture_changed = benchmark_input_digests(cases, fixture, source_root=source)
    assert fixture_changed["fixture_sha256"] != baseline["fixture_sha256"]

    payload = json.loads(cases.read_text(encoding="utf-8"))
    payload["cases"][0]["query"] += " changed"
    cases.write_text(json.dumps(payload), encoding="utf-8")
    cases_changed = benchmark_input_digests(cases, fixture, source_root=source)
    assert cases_changed["cases_sha256"] != fixture_changed["cases_sha256"]

    benchmark_source = source / "benchmark.py"
    benchmark_source.write_text(benchmark_source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    source_changed = benchmark_input_digests(cases, fixture, source_root=source)
    assert source_changed["retrieval_source_sha256"] != cases_changed["retrieval_source_sha256"]


def test_frozen_fixture_disables_checkout_eol_conversion():
    project = Path(__file__).resolve().parents[1]
    attributes = project / "benchmarks" / "fixtures" / "agent_repo" / ".gitattributes"
    assert attributes.read_text(encoding="utf-8").splitlines()[-1] == "* -text"
