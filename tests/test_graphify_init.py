from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "simple_repo"


def _env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["MIMRY_CACHE_HOME"] = str(tmp_path / "cache")
    return env


def _run(repo: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mimry.cli", "--root", str(repo), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_init_bootstraps_safe_graphify_output(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    env = _env(tmp_path)

    res = _run(repo, env, "init")

    assert res.returncode == 0, res.stderr
    graphify_dir = repo / ".mimry" / "graphify"
    assert graphify_dir.exists()
    assert (graphify_dir / "graph.json").exists()
    assert (graphify_dir / "GRAPH_REPORT.md").exists()
    assert (repo / "graphify-out").exists() is False


def test_refresh_runs_graphify_index_and_status(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    env = _env(tmp_path)
    init = _run(repo, env, "init")
    assert init.returncode == 0, init.stderr

    res = _run(repo, env, "refresh")

    assert res.returncode == 0, res.stderr
    assert "Refreshing MIMRY" in res.stdout
    assert "Graphify output:" in res.stdout
    assert "MIMRY indexing complete" in res.stdout
    assert "Index: current" in res.stdout
