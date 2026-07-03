from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "simple_repo"


def test_init_bootstraps_safe_graphify_output(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["MIMRY_CACHE_HOME"] = str(tmp_path / "cache")

    res = subprocess.run(
        [sys.executable, "-m", "mimry.cli", "--root", str(repo), "init"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert res.returncode == 0, res.stderr
    graphify_dir = repo / ".mimry" / "graphify"
    assert graphify_dir.exists()
    assert (graphify_dir / "graph.json").exists()
    assert (graphify_dir / "GRAPH_REPORT.md").exists()
    assert (repo / "graphify-out").exists() is False
