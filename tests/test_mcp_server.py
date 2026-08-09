from __future__ import annotations

import os
import contextlib
import io
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

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
        # A safety net so a regression that starts the server hangs the suite
        # instead of the machine -- not a performance assertion. Importing
        # fastmcp and tree-sitter-language-pack already costs ~9s on a cold
        # Windows runner, so 10s flaked under parallel load.
        timeout=60,
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
    from mimry.state import GENERATION_MANIFEST

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
    assert payload["initialized"] is True
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

    gen_data["schemaVersion"] = "999"
    generation_path.write_text(json.dumps(gen_data), encoding="utf-8")
    future_payload = mimry_digest(str(repo))
    assert future_payload["returncode"] == 2
    assert future_payload["initialized"] is True
    assert future_payload["error"]["code"] == "index_schema_unsupported"
    assert "newer" in future_payload["recommended"].lower()

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


def _degrade_schema(repo: Path, version: str) -> None:
    """Rewrite the active generation manifest to an older schema version."""
    from mimry.state import GENERATION_MANIFEST

    ptr = load_pointer(repo)
    manifest_path = Path(ptr["indexPath"]) / GENERATION_MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schemaVersion"] = version
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def _indexed_repo(tmp_path: Path, monkeypatch) -> Path:
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
    return repo


def _cli_status(repo: Path) -> tuple[int, str]:
    """Run the CLI status path and return (exit code, combined output)."""
    from mimry.cli import main as cli_main

    buf_out, buf_err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
        code = cli_main(["--root", str(repo), "status"])
    return code, buf_out.getvalue() + buf_err.getvalue()


@pytest.mark.parametrize(
    "state,expect_mcp_code,cli_must_mention,cli_must_not_mention",
    [
        ("old_schema", "index_schema_outdated", "rebuild", "corrupt"),
        ("future_schema", "index_schema_unsupported", "newer mimry client", "corrupt"),
        ("corruption", "state_corruption", "corrupt", None),
        ("missing", None, "init", None),
        ("healthy", None, None, "corrupt"),
    ],
)
def test_cli_and_mcp_agree_on_index_state(
    tmp_path: Path, monkeypatch, state, expect_mcp_code, cli_must_mention, cli_must_not_mention
):
    """CLI and MCP must classify the same four index states the same way.

    A schema upgrade is not corruption. Telling an agent its state is corrupt
    invites it to preserve and diagnose an index that only needs rebuilding.
    """
    if state == "missing":
        repo = tmp_path / "repo"
        shutil.copytree(FIXTURE, repo)
        monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    else:
        repo = _indexed_repo(tmp_path, monkeypatch)
        if state == "old_schema":
            _degrade_schema(repo, "2")
        elif state == "future_schema":
            _degrade_schema(repo, "999")
        elif state == "corruption":
            ptr = load_pointer(repo)
            (Path(ptr["indexPath"]) / "files.jsonl").write_text("{ not json", encoding="utf-8")

    mcp_payload = mimry_status(str(repo))
    cli_code, cli_text = _cli_status(repo)
    cli_lower = cli_text.lower()

    if expect_mcp_code is None:
        assert "error" not in mcp_payload or mcp_payload.get("error", {}).get("code") != "index_schema_outdated"
    else:
        assert mcp_payload["error"]["code"] == expect_mcp_code, f"{state}: MCP returned {mcp_payload.get('error')}"

    if cli_must_mention:
        assert cli_must_mention in cli_lower, f"{state}: CLI output missing {cli_must_mention!r}: {cli_text}"
    if cli_must_not_mention:
        assert cli_must_not_mention not in cli_lower, f"{state}: CLI wrongly said {cli_must_not_mention!r}: {cli_text}"

    if state == "old_schema":
        # Both surfaces must point at the same remedy and neither may call it corruption.
        assert "corrupt" not in mcp_payload["error"]["message"].lower()
        assert "mimry index" in mcp_payload["recommended"].lower()
        assert cli_code != 0


def test_successful_rebuild_clears_the_outdated_schema_state(tmp_path: Path, monkeypatch):
    """The recommended remedy must actually work on both surfaces."""
    repo = _indexed_repo(tmp_path, monkeypatch)
    _degrade_schema(repo, "2")
    assert mimry_status(str(repo))["error"]["code"] == "index_schema_outdated"

    from mimry.cli import main as cli_main

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        assert cli_main(["--root", str(repo), "index"]) == 0

    healed = mimry_status(str(repo))
    assert "error" not in healed or healed.get("error", {}).get("code") != "index_schema_outdated"
    code, text = _cli_status(repo)
    assert code == 0 and "corrupt" not in text.lower()
