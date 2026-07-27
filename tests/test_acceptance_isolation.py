from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from mimry.commands import cmd_init
from mimry.freshness import index_freshness
from mimry.graphify_artifacts import graphify_health, graphify_report_excerpt, graphify_rows
from mimry.graphify_wrapper import run_graphify_build
from mimry.indexer import write_index
from mimry.paths import graph_output_dir, graphify_output_dir
from mimry.scanner import scan
from mimry.storage import load_pointer


ROOT = Path(__file__).resolve().parents[1]
SENTINEL = "ACCEPTANCE_ONLY_SENTINEL_4F2A"


def run_cli(repo: Path, cache: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["MIMRY_CACHE_HOME"] = str(cache)
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "mimry.cli", "--root", str(repo), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def sqlite_text(path: Path) -> str:
    con = sqlite3.connect(path)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def test_acceptance_tests_are_absent_from_default_index_and_agent_outputs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    acceptance = repo / "acceptance_tests"
    acceptance.mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("def public_app():\n    return 'ok'\n", encoding="utf-8")
    (acceptance / "test_hidden_contract.py").write_text(
        f"def test_hidden_contract():\n    expected = '{SENTINEL}'\n    assert expected\n",
        encoding="utf-8",
    )
    cache = tmp_path / "cache"

    assert [path.relative_to(repo).as_posix() for path in scan(repo)] == ["src/app.py"]
    for args in (("init", "--skip-graphify"), ("index",), ("context", "public application")):
        result = run_cli(repo, cache, *args)
        assert result.returncode == 0, result.stderr
        assert SENTINEL not in result.stdout + result.stderr
        assert "test_hidden_contract.py" not in result.stdout + result.stderr

    rules = (repo / ".mimry" / "AGENT_RULES.md").read_text(encoding="utf-8")
    assert "acceptance_tests/" in rules
    assert "cooperative workflow isolation" in rules
    assert "not secrecy" in rules

    pointer = json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
    index = Path(pointer["indexPath"])
    persisted = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in index.rglob("*")
        if path.is_file() and path.suffix != ".sqlite"
    )
    persisted += sqlite_text(index / "mimry.sqlite")
    generated = (repo / ".mimry" / "mimry-out" / "context" / "latest.md").read_text(encoding="utf-8")
    for owned_output in (persisted, generated):
        assert SENTINEL not in owned_output
        assert "test_hidden_contract.py" not in owned_output
    assert "src/app.py" in persisted


def test_acceptance_tests_are_absent_from_graphify_handoff(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    acceptance = repo / "acceptance_tests"
    acceptance.mkdir(parents=True)
    (repo / "app.py").write_text("def app():\n    return 1\n", encoding="utf-8")
    (acceptance / "test_hidden.py").write_text(f"EXPECTED = '{SENTINEL}'\n", encoding="utf-8")
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    observed: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        handoff = Path(cmd[-1])
        observed["files"] = sorted(
            path.relative_to(handoff).as_posix() for path in handoff.rglob("*") if path.is_file()
        )
        observed["text"] = "\n".join(path.read_text(encoding="utf-8") for path in handoff.rglob("*") if path.is_file())
        out = graphify_output_dir(repo)
        out.mkdir(parents=True, exist_ok=True)
        (out / "graph.json").write_text('{"nodes": [], "edges": []}', encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("mimry.graphify_wrapper.graphify_source", lambda: "installed")
    monkeypatch.setattr("mimry.graphify_wrapper.subprocess.run", fake_run)

    assert run_graphify_build(repo, execute=True) == 0
    assert observed["files"] == ["app.py"]
    assert SENTINEL not in str(observed["text"])
    assert "acceptance_tests" not in str(observed["text"])


def test_existing_index_becomes_stale_when_policy_newly_ignores_acceptance_tests(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    acceptance = repo / "acceptance_tests"
    acceptance.mkdir(parents=True)
    (repo / "app.py").write_text("def app():\n    return 1\n", encoding="utf-8")
    (acceptance / "test_hidden.py").write_text(f"EXPECTED = '{SENTINEL}'\n", encoding="utf-8")
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    assert cmd_init(SimpleNamespace(root=repo, root_type="repo", skip_graphify=True)) == 0

    from mimry import security

    current_policy = security.HEAVY_IGNORES
    monkeypatch.setattr(security, "HEAVY_IGNORES", current_policy - {"acceptance_tests"})
    pointer = load_pointer(repo)
    assert pointer is not None
    write_index(repo, pointer)
    pointer = load_pointer(repo)
    assert pointer is not None
    assert "acceptance_tests/test_hidden.py" in (Path(pointer["indexPath"]) / "files.jsonl").read_text(encoding="utf-8")

    monkeypatch.setattr(security, "HEAVY_IGNORES", current_policy)
    freshness = index_freshness(repo, pointer)
    assert freshness["state"] == "stale"
    assert freshness["missing"] == ["acceptance_tests/test_hidden.py"]

    write_index(repo, pointer)
    pointer = load_pointer(repo)
    assert pointer is not None
    assert "acceptance_tests/test_hidden.py" not in (Path(pointer["indexPath"]) / "files.jsonl").read_text(
        encoding="utf-8"
    )


def test_existing_graphify_artifacts_filter_acceptance_sources_and_report(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    graph_dir = graph_output_dir(repo)
    graph_dir.mkdir(parents=True)
    graph = {
        "nodes": [
            {"id": "public", "label": "public_app", "source_file": "src/app.py"},
            {
                "id": "hidden",
                "label": SENTINEL,
                "source_file": "acceptance_tests/test_hidden.py",
            },
        ],
        "edges": [{"source": "public", "target": "hidden", "relation": SENTINEL}],
    }
    (graph_dir / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    (graph_dir / "manifest.json").write_text(
        json.dumps({"src/app.py": {}, "acceptance_tests/test_hidden.py": {}}), encoding="utf-8"
    )
    (graph_dir / "GRAPH_REPORT.md").write_text(
        f"# Graph report\n## Community Hubs\nacceptance_tests/test_hidden.py\n{SENTINEL}\n",
        encoding="utf-8",
    )

    assert graphify_rows(repo, SENTINEL) == []
    assert graphify_report_excerpt(repo) == ""
    health = graphify_health(repo, index_state="current")
    assert health["status"] == "stale"
    assert health["graph_nodes"] == 1
    assert health["graph_edges"] == 0
    assert health["policy_filtered_sources"] == ["acceptance_tests/test_hidden.py"]
