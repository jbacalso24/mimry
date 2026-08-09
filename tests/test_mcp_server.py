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
from mimry.storage import load_pointer, save_pointer

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


def test_mcp_status_reports_corrupt_pointer_without_raising(tmp_path: Path):
    repo = tmp_path / "repo"
    pointer = repo / ".mimry" / "pointer.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text("{truncated", encoding="utf-8")

    payload = mimry_status(str(repo))

    assert payload["initialized"] is False
    assert "Corrupt MIMRY state" in payload["state_error"]
    assert str(pointer) in payload["state_error"]
    assert "Preserve the corrupt state file" in payload["recommended"]


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
    assert "graph" in payload
    assert payload["graph"]["status"] in {"missing", "stale"}


def test_mcp_status_includes_graph_health_payload(tmp_path: Path, monkeypatch):
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
    graph_dir = repo / ".mimry" / "mimry-out" / "graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / "graph.json").write_text('{"nodes": [], "edges": []}\n', encoding="utf-8")
    (graph_dir / "GRAPH_REPORT.md").write_text("# Graph Report\n", encoding="utf-8")
    (graph_dir / "manifest.json").write_text("{}\n", encoding="utf-8")

    payload = mimry_status(str(repo))

    assert payload["graph"]["graph_exists"] is True
    assert payload["graph"]["report_exists"] is True
    assert payload["graph"]["manifest_exists"] is True
    assert payload["graphify"] == payload["graph"]


def test_mcp_init_accepts_legacy_skip_graphify_alias(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))

    payload = mimry_init(str(repo), skip_graphify=True)

    assert payload["returncode"] == 0
    assert payload["status"]["initialized"] is True


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

    assert payload["output"].replace("\\", "/").endswith("mimry-out/context/latest.md")
    assert payload["files"]
    text = (repo / ".mimry" / "mimry-out" / "context" / "latest.md").read_text(encoding="utf-8")
    for section in (
        "## Status Summary",
        "## Graph Relationships / Communities",
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
    assert route["risk_level"] in {"medium", "high"}
    assert "risk_gate_severity" in route
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
    assert payload["context_path"].replace("\\", "/").endswith(".mimry/mimry-out/context/latest.md")
    assert (repo / ".mimry" / "mimry-out" / "context" / "latest.md").exists()
    assert "MIMRY preflight complete" in payload["stdout"]


def test_mcp_refresh_rebuilds_index_and_reports_status(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    assert mimry_init(str(repo), skip_graph=True)["returncode"] == 0

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


def test_mcp_reports_outdated_schema_as_upgrade_not_corruption(tmp_path: Path, monkeypatch):
    """Outdated schema should report index_schema_outdated, not state_corruption."""
    from mimry.state import IndexSchemaMigrationError, GENERATION_MANIFEST, validate_generation

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

    # Get the actual pointer which now has a generationId
    actual_ptr = load_pointer(repo)
    generation_id = actual_ptr["generationId"]

    # Simulate old schema by modifying generation.json
    generation_path = Path(actual_ptr["indexPath"]) / GENERATION_MANIFEST
    gen_data = {
        "schemaVersion": "2",  # Old incompatible schema
        "generationId": generation_id,  # Must match the pointer
        "createdAt": "test",
        "artifacts": {},  # Empty artifacts dict (won't be checked since we error on schema first)
    }
    generation_path.write_text(__import__("json").dumps(gen_data), encoding="utf-8")

    payload = mimry_find("auth", str(repo))

    assert payload["returncode"] == 2
    assert payload["error"]["code"] == "index_schema_outdated"
    assert "outdated" in payload["error"]["message"].lower() or "rebuild" in payload["error"]["message"].lower()
    assert "corrupt" not in payload["error"]["message"].lower()
    assert "mimry index" in payload["error"]["message"].lower()


def test_mcp_still_reports_real_corruption_as_corruption(tmp_path: Path, monkeypatch):
    """Real corruption should still report state_corruption code."""
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

    # Corrupt a file to simulate real corruption (must corrupt a file that will be checksum-validated)
    actual_ptr = load_pointer(repo)
    files_path = Path(actual_ptr["indexPath"]) / "files.jsonl"
    files_path.write_bytes(b"corrupted bytes that are not JSON")

    payload = mimry_find("auth", str(repo))

    assert payload["returncode"] == 2
    assert payload["error"]["code"] == "state_corruption"
    assert "corrupt" in payload["error"]["message"].lower()


def test_mcp_tool_list_is_backward_compatible(tmp_path: Path):
    """Every existing tool must still be registered, plus new digest tool."""
    from mimry.mcp_server import mimry_symbol, mimry_semantic, mimry_digest

    assert callable(mimry_status)
    assert callable(mimry_find)
    assert callable(mimry_semantic)
    assert callable(mimry_symbol)
    assert callable(mimry_init)
    assert callable(mimry_refresh)
    assert callable(mimry_preflight)
    assert callable(mimry_explain)
    assert callable(mimry_path)
    assert callable(mimry_why)
    assert callable(mimry_feedback)
    assert callable(mimry_route)
    assert callable(mimry_brief)
    assert callable(mimry_context)
    assert callable(mimry_digest)


def test_mcp_digest_tool_matches_cli_digest(tmp_path: Path, monkeypatch):
    """MCP digest tool must return same digest as CLI 'mimry digest'."""
    from mimry.mcp_server import mimry_digest
    from mimry.digest import canonical_digest

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

    actual_ptr = load_pointer(repo)
    mcp_payload = mimry_digest(str(repo))
    cli_digest = canonical_digest(Path(actual_ptr["indexPath"]), actual_ptr.get("rootId"))

    assert mcp_payload["returncode"] == 0
    assert mcp_payload["canonical_state"]["digest"] == cli_digest
    assert mcp_payload["canonical_state"]["schema_version"] == "4"
    assert mcp_payload["operational_metadata"]["root_id"] == actual_ptr["rootId"]


def test_mcp_digest_tool_declares_canonical_and_operational_fields(tmp_path: Path, monkeypatch):
    """Digest tool must clearly label canonical vs operational fields."""
    from mimry.mcp_server import mimry_digest

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

    payload = mimry_digest(str(repo))

    assert payload["returncode"] == 0
    assert "canonical_state" in payload
    assert payload["canonical_state"]["digest"]
    assert payload["canonical_state"]["schema_version"]
    assert "operational_metadata" in payload
    assert payload["operational_metadata"]["root_id"]
    assert payload["operational_metadata"]["generation_id"]


def test_mcp_digest_tool_structured_errors(tmp_path: Path, monkeypatch):
    """Digest tool must return structured errors for missing, outdated, and corrupt indexes."""
    from mimry.mcp_server import mimry_digest

    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))

    # Test missing index
    missing_payload = mimry_digest(str(repo))
    assert missing_payload["returncode"] == 2
    assert missing_payload["error"]["code"] == "uninitialized"

    # Test outdated schema
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

    actual_ptr = load_pointer(repo)
    generation_id = actual_ptr["generationId"]
    from mimry.state import GENERATION_MANIFEST
    generation_path = Path(actual_ptr["indexPath"]) / GENERATION_MANIFEST
    gen_data = {
        "schemaVersion": "2",
        "generationId": generation_id,
        "createdAt": "test",
        "artifacts": {},
    }
    generation_path.write_text(__import__("json").dumps(gen_data), encoding="utf-8")

    outdated_payload = mimry_digest(str(repo))
    assert outdated_payload["returncode"] == 2
    assert outdated_payload["error"]["code"] == "index_schema_outdated"

    # Test corrupt index
    repo2 = tmp_path / "repo2"
    shutil.copytree(FIXTURE, repo2)
    root_id2 = str(uuid.uuid4())
    ptr2 = {
        "rootId": root_id2,
        "rootPath": str(repo2),
        "rootType": "repo",
        "indexPath": str(idx_path(root_id2)),
        "createdAt": "test",
        "lastIndexedAt": None,
        "schemaVersion": 1,
    }
    save_pointer(repo2, ptr2)
    write_index(repo2, ptr2)

    actual_ptr2 = load_pointer(repo2)
    files_path = Path(actual_ptr2["indexPath"]) / "files.jsonl"
    files_path.write_bytes(b"corrupted")

    corrupt_payload = mimry_digest(str(repo2))
    assert corrupt_payload["returncode"] == 2
    assert corrupt_payload["error"]["code"] == "state_corruption"
