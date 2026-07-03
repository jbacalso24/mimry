from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

from mimry import mcp_server
from mimry.indexer import write_index
from mimry.mcp_server import mimry_find, mimry_status
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
