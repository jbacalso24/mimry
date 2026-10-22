from __future__ import annotations

import errno
import sqlite3
from pathlib import Path

import pytest
from test_history import _share_extension_repo
from test_state_recovery import (
    _deny_cache_file_writes,
    _deny_cache_writes,
    _deny_mcp_refresh,
    _initialized_repo,
    _stale_repo_with_cache,
)

from mimry import cli, mcp_tools
from mimry.cache_safety import cache_writable
from mimry.mcp_tools import mimry_feedback, mimry_history, mimry_status
from mimry.storage import load_pointer


def test_readonly_indexes_are_detected_even_when_cache_root_is_writable(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    indexes = cache / "indexes"
    indexes.mkdir(parents=True)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    original = Path.open

    def denied(path, mode="r", *args, **kwargs):
        if path.parent == indexes and "x" in mode:
            raise PermissionError(errno.EACCES, "denied", str(path))
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied)
    assert not cache_writable()
    assert not list(cache.rglob(".write-probe-*"))


def test_write_probe_is_removed_when_interrupted(tmp_path, monkeypatch):
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path))
    original = Path.open

    class Interrupted:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self.handle

        def __exit__(self, *args):
            self.handle.close()
            raise KeyboardInterrupt

    def interrupted(path, mode="r", *args, **kwargs):
        handle = original(path, mode, *args, **kwargs)
        return Interrupted(handle) if "x" in mode else handle

    monkeypatch.setattr(Path, "open", interrupted)
    with pytest.raises(KeyboardInterrupt):
        cache_writable()
    assert not list(tmp_path.glob(".write-probe-*"))


def test_mcp_status_reports_readonly_cache(tmp_path, monkeypatch):
    repo, cache = _stale_repo_with_cache(tmp_path, monkeypatch)
    _deny_cache_file_writes(monkeypatch, cache)
    result = mimry_status(str(repo))
    assert result["cache_read_only"] is True
    assert "read-only cache" in result["stdout"]


def test_mcp_feedback_returns_cache_error_not_permission_exception(tmp_path, monkeypatch):
    repo, cache = _stale_repo_with_cache(tmp_path, monkeypatch)
    _deny_cache_file_writes(monkeypatch, cache)
    result = mimry_feedback("login", root=str(repo))
    assert result["returncode"] == 2
    assert result["error"]["code"] == "cache_read_only"
    assert "MIMRY_CACHE_HOME" in result["recommended"]


@pytest.mark.parametrize("surface", ["cli", "mcp"])
def test_history_answers_without_writing_cached_commits(tmp_path, monkeypatch, capsys, surface):
    repo, cache = _share_extension_repo(tmp_path)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    # Populate history using the regular writable command first.
    assert cli.main(["--root", str(repo), "history", "share extension"]) == 0
    capsys.readouterr()
    database = Path(load_pointer(repo)["indexPath"]) / "mimry.sqlite"
    before = database.read_bytes()
    _deny_cache_file_writes(monkeypatch, cache)
    _deny_cache_writes(monkeypatch, cache)
    _deny_mcp_refresh(monkeypatch, cache)
    original = sqlite3.connect

    def readonly(path, *args, **kwargs):
        con = original(path, *args, **kwargs)
        con.execute("PRAGMA query_only=ON")
        return con

    monkeypatch.setattr(sqlite3, "connect", readonly)
    if surface == "cli":
        assert cli.main(["--root", str(repo), "history", "share extension"]) == 0
        assert "Remove the Share Extension target" in capsys.readouterr().out
    else:
        result = mimry_history("share extension", root=str(repo))
        assert result["commits"]
        assert "read-only cache" in result["stdout"]
    assert database.read_bytes() == before


@pytest.mark.parametrize(
    "command", [["path", "src/auth/middleware.py", "src/auth/session.py"], ["callers", "login"]]
)
def test_graph_advice_does_not_promise_readonly_refresh(tmp_path, monkeypatch, capsys, command):
    repo, cache = _stale_repo_with_cache(tmp_path, monkeypatch)
    _deny_cache_writes(monkeypatch, cache)
    _deny_cache_file_writes(monkeypatch, cache)
    cli.main(["--root", str(repo), *command])
    output = capsys.readouterr().out
    assert "Run `mimry refresh`" not in output
    assert "read-only" in output


def _count_cache_probes(monkeypatch) -> list[str]:
    """Record every call to cache_writable, wherever it is imported."""
    calls: list[str] = []

    def spy() -> bool:
        calls.append("probe")
        return True

    for target in ("mimry.cache_safety", "mimry.mcp_tools", "mimry.commands"):
        monkeypatch.setattr(f"{target}.cache_writable", spy)
    return calls


def test_status_on_current_index_does_not_probe_a_writable_cache(tmp_path, monkeypatch):
    repo, cache = _initialized_repo(tmp_path)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    probes = _count_cache_probes(monkeypatch)

    result = mimry_status(str(repo))

    assert result["index_state"] == "current"
    assert "cache_read_only" not in result
    assert probes == []


def test_cli_history_on_writable_cache_does_not_probe(tmp_path, monkeypatch, capsys):
    repo, cache = _share_extension_repo(tmp_path)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    probes = _count_cache_probes(monkeypatch)

    assert cli.main(["--root", str(repo), "history", "share extension"]) == 0

    assert "Remove the Share Extension target" in capsys.readouterr().out
    assert probes == []


def test_mcp_history_on_a_current_index_does_not_call_it_stale(tmp_path, monkeypatch):
    # The refresh passes; only history's exclusive lock is refused.
    repo, cache = _share_extension_repo(tmp_path)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    assert cli.main(["--root", str(repo), "history", "share extension"]) == 0
    original = mcp_tools.active_index_pointer
    denied = cache / "indexes" / "locked" / "operation.lock"

    def pointer(root, *, exclusive=False):
        if exclusive:
            raise PermissionError(errno.EACCES, "Permission denied", str(denied))
        return original(root, exclusive=exclusive)

    monkeypatch.setattr(mcp_tools, "active_index_pointer", pointer)
    result = mimry_history("share extension", root=str(repo))
    assert result["commits"]
    assert "stale" not in (result.get("stdout") or "")
