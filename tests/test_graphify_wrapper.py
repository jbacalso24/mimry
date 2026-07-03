from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "simple_repo"


def run_cli(work: Path, cache: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["MIMRY_CACHE_HOME"] = str(cache)
    return subprocess.run(
        [sys.executable, "-m", "mimry.cli", "--root", str(work), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def copy_fixture(tmp_path: Path) -> Path:
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURE, dest)
    return dest


def test_graphify_status_reports_pinned_submodule():
    result = subprocess.run(
        [sys.executable, "-m", "mimry.cli", "graphify", "status"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Graphify status" in result.stdout
    assert "Source:" in result.stdout
    assert "44c0a5e33c7011813dcebf1a8850c1c6005bf500" in result.stdout


def test_graphify_build_dry_run_is_safe_and_points_to_mimry_output(tmp_path: Path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init").returncode == 0
    result = run_cli(repo, cache, "graphify", "build", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "DRY RUN" in result.stdout
    assert "GRAPHIFY_OUT" in result.stdout
    assert str(repo / ".mimry" / "graphify") in result.stdout
    assert (
        "Command: graphify update <root>" in result.stdout
        or "Command: python -m graphify update <root>" in result.stdout
    )
    assert "graphify install" not in result.stdout
    assert "graphify hook" not in result.stdout
