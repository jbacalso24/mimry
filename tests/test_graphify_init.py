from __future__ import annotations

import os
import json
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
    ptr = json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
    internal_graph_dir = Path(ptr["indexPath"]) / "graphify"
    visible_graph_dir = repo / ".mimry" / "mimry-out" / "graph"
    assert internal_graph_dir.exists()
    assert visible_graph_dir.exists()
    assert (visible_graph_dir / "graph.json").exists()
    assert (visible_graph_dir / "GRAPH_REPORT.md").exists()
    assert (repo / ".mimry" / "graphify").exists() is False
    assert (repo / "graphify-out").exists() is False


def test_refresh_runs_graphify_index_and_status(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    env = _env(tmp_path)
    init = _run(repo, env, "init")
    assert init.returncode == 0, init.stderr

    visible_graph_dir = repo / ".mimry" / "mimry-out" / "graph"
    shutil.rmtree(visible_graph_dir)
    res = _run(repo, env, "refresh")

    assert res.returncode == 0, res.stderr
    assert "Refreshing MIMRY" in res.stdout
    assert "MIMRY internal graph build complete." in res.stdout
    assert "MIMRY indexing complete" in res.stdout
    assert "Index: current" in res.stdout
    assert (visible_graph_dir / "graph.json").exists()
    assert (repo / ".mimry" / "graphify").exists() is False

    status = _run(repo, env, "status")

    assert status.returncode == 0, status.stderr
    assert "Status: current" in status.stdout
    assert "Graph source changes: 0 changed, 0 missing" in status.stdout
    assert "MIMRY graph artifacts stale/missing: no" in status.stdout

    changed_source = repo / "src" / "auth" / "session.py"
    changed_source.write_text(changed_source.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    stale_status = _run(repo, env, "status")

    assert stale_status.returncode == 2, stale_status.stderr
    assert "Status: stale" in stale_status.stdout
    assert "Graph source changes: 1 changed, 0 missing" in stale_status.stdout
    assert "MIMRY graph artifacts stale/missing: yes" in stale_status.stdout
