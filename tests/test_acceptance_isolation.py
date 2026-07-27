from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from mimry.commands import cmd_context, cmd_find, cmd_init, cmd_symbol
from mimry.freshness import index_freshness
from mimry.graphify_artifacts import graphify_health, graphify_report_excerpt, graphify_rows
from mimry.graphify_wrapper import run_graphify_build
from mimry.indexer import write_index
from mimry.mcp_server import mimry_context, mimry_find, mimry_semantic, mimry_status, mimry_symbol
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


def test_existing_index_becomes_stale_when_policy_newly_ignores_acceptance_tests(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = tmp_path / "repo"
    acceptance = repo / "acceptance_tests"
    acceptance.mkdir(parents=True)
    (repo / "app.py").write_text("def app():\n    return 1\n", encoding="utf-8")
    (acceptance / "test_hidden.py").write_text(
        f"def hidden_acceptance_symbol():\n    return '{SENTINEL}'\n", encoding="utf-8"
    )
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
    assert freshness["missing"] == []
    assert freshness["policy_excluded_count"] == 1
    assert all("acceptance_tests" not in record.get("rel_path", "") for record in freshness["files"])
    assert all(symbol["name"] != "hidden_acceptance_symbol" for symbol in freshness["symbols"])

    capsys.readouterr()
    assert cmd_find(SimpleNamespace(root=repo, query="hidden_acceptance_symbol", limit=10, semantic=False)) == 0
    assert cmd_symbol(SimpleNamespace(root=repo, name="hidden_acceptance_symbol")) == 0
    assert cmd_context(SimpleNamespace(root=repo, query="hidden acceptance contract", semantic=True)) == 0
    cli_output = capsys.readouterr().out
    context_output = (repo / ".mimry" / "mimry-out" / "context" / "latest.md").read_text(encoding="utf-8")
    assert SENTINEL not in cli_output + context_output
    assert "acceptance_tests/test_hidden.py" not in cli_output + context_output

    mcp_find = mimry_find("hidden_acceptance_symbol", str(repo), semantic=True)
    mcp_symbols = mimry_symbol("hidden_acceptance_symbol", str(repo))
    mcp_context = mimry_context("hidden acceptance contract", str(repo), semantic=True)
    mcp_text = json.dumps((mcp_find, mcp_context), sort_keys=True)
    assert SENTINEL not in mcp_text
    assert "acceptance_tests/test_hidden.py" not in mcp_text
    assert mcp_symbols["symbols"] == []
    assert mimry_semantic("hidden_acceptance_symbol", str(repo))["results"] == []
    status_text = json.dumps(mimry_status(str(repo)), sort_keys=True)
    assert SENTINEL not in status_text
    assert "acceptance_tests/test_hidden.py" not in status_text
    assert freshness["graph"]["nodes"] == [
        node for node in freshness["graph"]["nodes"] if "acceptance_tests" not in str(node)
    ]

    (acceptance / "test_hidden.py").unlink()
    deleted_freshness = index_freshness(repo, pointer)
    assert deleted_freshness["missing"] == []
    assert deleted_freshness["policy_excluded_count"] == 1
    deleted_status = json.dumps(mimry_status(str(repo)), sort_keys=True)
    assert "acceptance_tests/test_hidden.py" not in deleted_status

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
        f"# Graph report for acceptance_tests/test_hidden.py\n"
        f"- Built from commit: `acceptance_tests/hidden`\n"
        f"## Community Hubs\nsrc/acceptance_tests/test_hidden.py\n{SENTINEL}\n",
        encoding="utf-8",
    )

    assert graphify_rows(repo, SENTINEL) == []
    assert graphify_report_excerpt(repo) == ""
    health = graphify_health(repo, index_state="current")
    assert health["status"] == "stale"
    assert health["graph_nodes"] == 1
    assert health["graph_edges"] == 0
    assert health["policy_filtered_source_count"] == 1
    assert "acceptance_tests/test_hidden.py" not in json.dumps(health, sort_keys=True)


def test_ignored_path_detection_covers_relative_absolute_and_windows_forms() -> None:
    from mimry.security import text_mentions_ignored_path

    for path in (
        "acceptance_tests/test_hidden.py",
        "./acceptance_tests/test_hidden.py",
        "src/acceptance_tests/test_hidden.py",
        "/tmp/repo/acceptance_tests/test_hidden.py",
        r"C:\\repo\\acceptance_tests\\test_hidden.py",
    ):
        assert text_mentions_ignored_path(path)


def test_graphify_report_keeps_benign_heavy_ignore_words(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    graph_dir = graph_output_dir(repo)
    graph_dir.mkdir(parents=True)
    report = "# Graph report\n## Community Hubs\nCompleted build target for vendor integration\n"
    (graph_dir / "GRAPH_REPORT.md").write_text(report, encoding="utf-8")

    assert "Completed build target for vendor integration" in graphify_report_excerpt(repo)
