from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from mimry import mcp_server
from mimry.indexer import write_index
from mimry.mcp_server import (
    mimry_context,
    mimry_brief,
    mimry_explain,
    mimry_feedback,
    mimry_find,
    mimry_init,
    mimry_path,
    mimry_preflight,
    mimry_route,
    mimry_refresh,
    mimry_status,
    mimry_why,
)
from mimry.paths import idx_path
from mimry.storage import save_pointer

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "simple_repo"


def test_mcp_server_imports_and_registers_tools():
    assert mcp_server.mcp.name == "MIMRY"
    # FastMCP stores tools internally, but import success and callable decorated
    # functions are the minimum guard for packaging/console-script regressions.
    assert callable(mimry_status)
    assert callable(mimry_find)
    assert callable(mimry_init)
    assert callable(mimry_refresh)
    assert callable(mimry_preflight)
    assert callable(mimry_explain)
    assert callable(mimry_path)
    assert callable(mimry_why)
    assert callable(mimry_feedback)
    assert callable(mimry_route)
    assert callable(mimry_brief)


def test_mimry_mcp_help_does_not_start_server():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    res = subprocess.run(
        [sys.executable, "-m", "mimry.mcp_server", "--help"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert "Run the MIMRY MCP stdio server" in res.stdout
    assert "Starting MCP server" not in res.stderr


def test_mcp_status_uninitialized_root(tmp_path: Path):
    payload = mimry_status(str(tmp_path))
    assert payload["initialized"] is False
    assert payload["root"] == str(tmp_path.resolve())


def test_mcp_status_uses_hash_freshness_for_same_size_rewrite(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    root_id = str(uuid.uuid4())
    ptr = {
        "rootId": root_id,
        "rootPath": str(repo),
        "rootType": "repo",
        "indexPath": str(idx_path(root_id)),
        "createdAt": "test",
        "lastIndexedAt": None,
        "schemaVersion": 1,
    }
    save_pointer(repo, ptr)
    write_index(repo, ptr)

    target = repo / "src" / "auth" / "session.py"
    original_stat = target.stat()
    text = target.read_text(encoding="utf-8")
    changed = text.replace("pass", "True")
    assert len(changed.encode()) == len(text.encode())
    target.write_text(changed, encoding="utf-8")
    os.utime(target, (original_stat.st_atime, original_stat.st_mtime))

    payload = mimry_status(str(repo))

    assert payload["index_state"] == "stale"
    assert payload["changed_files"] == ["src/auth/session.py"]
    assert "graphify" in payload
    assert payload["graphify"]["status"] in {"missing", "stale"}


def test_mcp_status_includes_graphify_health_payload(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    root_id = str(uuid.uuid4())
    ptr = {
        "rootId": root_id,
        "rootPath": str(repo),
        "rootType": "repo",
        "indexPath": str(idx_path(root_id)),
        "createdAt": "test",
        "lastIndexedAt": None,
        "schemaVersion": 1,
    }
    save_pointer(repo, ptr)
    write_index(repo, ptr)
    graphify_dir = repo / ".mimry" / "graphify"
    graphify_dir.mkdir(parents=True)
    (graphify_dir / "graph.json").write_text('{"nodes": [], "edges": []}\n', encoding="utf-8")
    (graphify_dir / "GRAPH_REPORT.md").write_text("# Graph Report\n", encoding="utf-8")
    (graphify_dir / "manifest.json").write_text("{}\n", encoding="utf-8")

    payload = mimry_status(str(repo))

    assert payload["graphify"]["graph_exists"] is True
    assert payload["graphify"]["report_exists"] is True
    assert payload["graphify"]["manifest_exists"] is True
    assert payload["graphify"]["source"] in {"vendor", "installed", "missing"}
    assert payload["graphify"]["pinned_commit"]


def test_mcp_context_uses_evidence_grade_context_pack_writer(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    root_id = str(uuid.uuid4())
    ptr = {
        "rootId": root_id,
        "rootPath": str(repo),
        "rootType": "repo",
        "indexPath": str(idx_path(root_id)),
        "createdAt": "test",
        "lastIndexedAt": None,
        "schemaVersion": 1,
    }
    save_pointer(repo, ptr)
    write_index(repo, ptr)

    payload = mimry_context("fix auth session", str(repo))

    assert payload["output"].endswith("mimry-out/context/latest.md")
    assert payload["files"]
    text = (repo / ".mimry" / "mimry-out" / "context" / "latest.md").read_text(encoding="utf-8")
    for section in (
        "## Status Summary",
        "## Graphify Relationships / Communities",
        "## Likely Edit Surfaces",
        "## Suggested Verification Commands",
        "## Final Report Checklist",
    ):
        assert section in text
    assert "After verification, run `mimry feedback ...`" in text


def test_mcp_route_and_brief_return_structured_payloads(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    root_id = str(uuid.uuid4())
    ptr = {
        "rootId": root_id,
        "rootPath": str(repo),
        "rootType": "repo",
        "indexPath": str(idx_path(root_id)),
        "createdAt": "test",
        "lastIndexedAt": None,
        "schemaVersion": 1,
    }
    save_pointer(repo, ptr)
    write_index(repo, ptr)

    route = mimry_route("fix FastAPI auth bug", str(repo))
    brief = mimry_brief("fix auth", "backend", str(repo))

    assert route["recommended_agent"] == "backend"
    assert route["risk_approval_gates"]
    assert route["likely_files"]
    assert brief["agent"] == "backend"
    assert brief["output"].endswith("brief-backend.md")
    assert (repo / ".mimry" / "mimry-out" / "context" / "brief-backend.md").exists()


def test_mcp_preflight_initializes_and_writes_context(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))

    payload = mimry_preflight("fix auth session", str(repo))

    assert payload["returncode"] == 0
    assert payload["initialized"] is True
    assert payload["index_state"] == "current"
    assert payload["context_path"].endswith(".mimry/mimry-out/context/latest.md")
    assert (repo / ".mimry" / "mimry-out" / "context" / "latest.md").exists()
    assert "MIMRY preflight complete" in payload["stdout"]


def test_mcp_refresh_rebuilds_index_and_reports_status(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    assert mimry_init(str(repo), skip_graphify=True)["returncode"] == 0

    payload = mimry_refresh(str(repo))

    assert payload["returncode"] == 0
    assert payload["status"]["index_state"] == "current"
    assert payload["status"]["files_indexed"] > 0
    assert "MIMRY indexing complete" in payload["stdout"]


def test_mcp_explain_path_why_and_feedback_tools_return_agent_payloads(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    assert mimry_preflight("fix auth session", str(repo))["returncode"] == 0

    explain = mimry_explain("fix auth session", str(repo), limit=3)
    why = mimry_why("src/auth/session.py", "fix auth session", str(repo))
    path = mimry_path("src/auth/session.py", "tests/test_session.py", str(repo))
    feedback = mimry_feedback(
        query="fix auth session",
        root=str(repo),
        opened=["src/auth/session.py"],
        changed=["src/auth/session.py"],
        outcome="passed",
        verification="pytest passed",
    )

    assert explain["returncode"] == 0 and "MIMRY explain" in explain["stdout"]
    assert why["returncode"] == 0 and "MIMRY why" in why["stdout"]
    assert path["returncode"] == 0
    assert "No path was invented" in path["stdout"] or "Path found" in path["stdout"]
    assert feedback["returncode"] == 0
    assert feedback["feedback"]["outcome"] == "passed"
    assert "src/auth/session.py" in feedback["feedback"]["changed_paths"]
