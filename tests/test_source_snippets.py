from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from test_mimry_cli import copy_fixture, run_cli

import mimry.source_snippets as source_snippets
from mimry.indexer import write_index
from mimry.mcp_server import mimry_find
from mimry.paths import idx_path
from mimry.source_snippets import with_source_snippets
from mimry.storage import load_pointer, save_pointer


def _indexed(tmp_path: Path, monkeypatch):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    root_id = str(uuid.uuid4())
    pointer = {
        "rootId": root_id,
        "rootPath": str(repo),
        "rootType": "repo",
        "indexPath": str(idx_path(root_id)),
        "createdAt": "test",
        "lastIndexedAt": None,
        "schemaVersion": 1,
    }
    save_pointer(repo, pointer)
    write_index(repo, pointer)
    return repo, cache, load_pointer(repo)


def test_cli_snippets_are_opt_in_bounded_and_exact(tmp_path, monkeypatch):
    repo, cache, _ = _indexed(tmp_path, monkeypatch)

    compact = run_cli(repo, cache, "find", "create_session", "--limit", "1")
    first = run_cli(
        repo,
        cache,
        "find",
        "create_session",
        "--limit",
        "1",
        "--snippets",
        "--snippet-lines",
        "1",
        "--snippet-chars",
        "120",
        "--snippet-total-lines",
        "1",
        "--snippet-total-chars",
        "120",
    )
    second = run_cli(
        repo,
        cache,
        "find",
        "create_session",
        "--limit",
        "1",
        "--snippets",
        "--snippet-lines",
        "1",
        "--snippet-chars",
        "120",
        "--snippet-total-lines",
        "1",
        "--snippet-total-chars",
        "120",
    )

    assert compact.returncode == 0
    assert "def create_session" not in compact.stdout
    assert first.returncode == 0, first.stdout + first.stderr
    assert "src/auth/session.py:1-1" in first.stdout
    assert "def create_session" in first.stdout
    assert "[snippet truncated]" in first.stdout
    assert first.stdout == second.stdout


@pytest.mark.parametrize(
    "argument",
    ["--snippet-lines", "--snippet-chars", "--snippet-total-lines", "--snippet-total-chars"],
)
def test_cli_rejects_nonpositive_snippet_limits(tmp_path, monkeypatch, argument):
    repo, cache, _ = _indexed(tmp_path, monkeypatch)
    result = run_cli(repo, cache, "find", "session", "--snippets", argument, "0")
    assert result.returncode == 2
    assert "Invalid snippet limits" in result.stderr


def test_mcp_parity_caps_and_changed_deleted_fail_closed(tmp_path, monkeypatch):
    repo, _, _ = _indexed(tmp_path, monkeypatch)

    compact = mimry_find("create_session", str(repo), limit=1)
    payload = mimry_find(
        "create_session",
        str(repo),
        limit=1,
        snippets=True,
        snippet_lines=2,
        snippet_chars=30,
        snippet_total_lines=2,
        snippet_total_chars=30,
    )
    assert "snippet" not in compact["results"][0]
    snippet = payload["results"][0]["snippet"]
    assert snippet["start_line"] == 1
    assert snippet["end_line"] in {1, 2}
    assert len(snippet["text"]) <= 30
    assert snippet["truncated"] is True
    assert payload == mimry_find(
        "create_session",
        str(repo),
        limit=1,
        snippets=True,
        snippet_lines=2,
        snippet_chars=30,
        snippet_total_lines=2,
        snippet_total_chars=30,
    )

    target = repo / "src/auth/session.py"
    target.write_text(target.read_text() + "\n# changed\n", encoding="utf-8")
    changed = mimry_find("create_session", str(repo), limit=1, snippets=True)
    assert changed["results"][0]["snippet"]["status"] == "source_changed"
    target.unlink()
    deleted = mimry_find("create_session", str(repo), limit=10, snippets=True)
    assert all(row["path"] != "src/auth/session.py" for row in deleted["results"])


def test_snippet_reader_rejects_credentials_symlink_and_untrusted_paths(tmp_path, monkeypatch):
    repo, _, pointer = _indexed(tmp_path, monkeypatch)
    outside = tmp_path / "outside.py"
    outside.write_text("outside_marker = True\n", encoding="utf-8")
    link = repo / "linked.py"
    link.symlink_to(outside)
    credential = repo / ".env"
    credential.write_text("PASSWORD=fake-test-value\n", encoding="utf-8")

    rows = [
        {"path": "../outside.py", "score": 1, "reason": "test"},
        {"path": "linked.py", "score": 1, "reason": "test"},
        {"path": ".env", "score": 1, "reason": "test"},
    ]
    enriched = with_source_snippets(rows, root=repo, pointer=pointer, query="marker")
    assert [row["snippet"]["status"] for row in enriched] == [
        "unsafe_path",
        "source_unavailable",
        "source_unavailable",
    ]
    assert "outside_marker" not in repr(enriched)
    assert "fake-test-value" not in repr(enriched)


def test_snippet_reader_rejects_binary_and_secret_content(tmp_path, monkeypatch):
    repo, _, pointer = _indexed(tmp_path, monkeypatch)
    binary = repo / "asset.bin"
    binary.write_bytes(b"valid utf8 but binary\n")
    secret = repo / "notes.txt"
    secret.write_text("PASSWORD=fake-test-value\n", encoding="utf-8")
    write_index(repo, pointer)
    pointer = load_pointer(repo)

    rows = [{"path": "asset.bin"}, {"path": "notes.txt"}]
    enriched = with_source_snippets(rows, root=repo, pointer=pointer, query="value")

    assert [row["snippet"]["status"] for row in enriched] == [
        "source_unavailable",
        "source_unavailable",
    ]
    assert "fake-test-value" not in repr(enriched)


def test_snippet_reader_detects_change_between_freshness_and_read(tmp_path, monkeypatch):
    repo, _, pointer = _indexed(tmp_path, monkeypatch)
    target = repo / "src/auth/session.py"
    original_read = source_snippets.read_snapshot

    def changed_read(path, *, root):
        if Path(path) == target:
            target.write_text("replacement safe text\n", encoding="utf-8")
        return original_read(path, root=root)

    monkeypatch.setattr(source_snippets, "read_snapshot", changed_read)
    enriched = with_source_snippets(
        [{"path": "src/auth/session.py"}], root=repo, pointer=pointer, query="session"
    )

    assert enriched[0]["snippet"]["status"] == "source_changed"
    assert "replacement safe text" not in repr(enriched)


def test_blank_line_range_and_global_budgets_are_exact(tmp_path, monkeypatch):
    repo, _, pointer = _indexed(tmp_path, monkeypatch)
    target = repo / "src/auth/session.py"
    target.write_text("\nsecond\nthird\n", encoding="utf-8")
    write_index(repo, pointer)
    pointer = load_pointer(repo)

    enriched = with_source_snippets(
        [{"path": "src/auth/session.py"}, {"path": "src/auth/session.py"}],
        root=repo,
        pointer=pointer,
        query="no-match",
        lines_per_result=2,
        chars_per_result=1,
        total_lines=1,
        total_chars=1,
    )

    assert enriched[0]["snippet"] == {
        "start_line": 1,
        "end_line": 1,
        "text": "",
        "truncated": True,
    }
    assert enriched[1]["snippet"] == {"status": "global_limit", "truncated": True}


def test_mcp_rejects_invalid_budget(tmp_path, monkeypatch):
    repo, _, _ = _indexed(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="snippet_lines must be a positive integer"):
        mimry_find("session", str(repo), snippets=True, snippet_lines=0)
