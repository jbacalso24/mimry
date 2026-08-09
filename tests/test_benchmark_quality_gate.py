"""Quality gate for the frozen usefulness benchmark.

Asserts that the committed benchmark result represents a passing state
with locked thresholds to prevent silent degradation.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from mimry.benchmark import DEFAULT_THRESHOLDS, SCHEMA_VERSION


def _digest_fixture(fixture: Path) -> str:
    """Hash all files in the fixture directory."""
    digest = hashlib.sha256()
    for path in sorted(p for p in fixture.rglob("*") if p.is_file()):
        digest.update(path.relative_to(fixture).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def test_benchmark_result_is_passing_and_stable():
    """Committed benchmark result must be PASS with matching thresholds."""
    project = Path(__file__).resolve().parents[1]
    result_path = project / "benchmarks" / "result.core.json"
    fixture_path = project / "benchmarks" / "fixtures" / "agent_repo"

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

    # Fixture hash must match current fixture to catch silent file changes
    current_fixture_hash = _digest_fixture(fixture_path)
    reported_hash = report.get("fixture_sha256")
    assert reported_hash == current_fixture_hash, (
        f"Fixture changed: reported {reported_hash}, current {current_fixture_hash}"
    )

    # Every threshold must match frozen defaults (prevent silent weakening)
    reported_thresholds = report.get("thresholds", {})
    for key, expected_value in DEFAULT_THRESHOLDS.items():
        reported_value = reported_thresholds.get(key)
        assert reported_value == expected_value, (
            f"Threshold {key} changed: {reported_value} != {expected_value} (DEFAULT_THRESHOLDS frozen)"
        )

    # All checks must pass
    checks = report.get("checks", {})
    for check_name, passed in checks.items():
        assert passed, f"Check {check_name} failed in committed benchmark result"
