"""The hook answers a search for a known symbol with its definition and
callers, read from the index (``mimry hook-check``)."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from mimry import hook
from mimry.hook import cmd_hook_check

ROOT = Path(__file__).resolve().parents[1]

ONE = """def helper(x):
    return leaf(x)


def leaf(x):
    return x


def main():
    return helper(1)
"""

TWO = """from app.one import helper


def helper2():
    return helper(2)
"""


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _mimry(cwd: Path, cache: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["MIMRY_CACHE_HOME"] = str(cache)
    return subprocess.run(
        [sys.executable, "-m", "mimry.cli", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A git repo with two small modules, indexed with the graph."""
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True)
    (root / "app" / "one.py").write_text(ONE, encoding="utf-8")
    (root / "app" / "two.py").write_text(TWO, encoding="utf-8")
    cache = tmp_path / "cache"
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "MIMRY Test")
    _git(root, "config", "user.email", "test@example.com")
    assert _mimry(root, cache, "init").returncode == 0
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "Initial commit")
    assert _mimry(root, cache, "index").returncode == 0
    return root


def _index(repo: Path) -> Path:
    pointer = json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
    return Path(pointer["indexPath"])


def _hook(monkeypatch, capsys, repo: Path, payload: dict) -> str:
    """The additional context the hook prints for ``payload``, or ""."""
    data = json.dumps(payload).encode("utf-8")
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(data)))
    assert cmd_hook_check(SimpleNamespace(root=str(repo))) == 0
    out = capsys.readouterr().out
    if not out.strip():
        return ""
    return json.loads(out)["hookSpecificOutput"]["additionalContext"]


def _grep(pattern: str, session: str = "s1", **tool_input) -> dict:
    return {
        "tool_name": "Grep",
        "session_id": session,
        "tool_input": {"pattern": pattern, **tool_input},
    }


def _shell(command: str, session: str = "s1") -> dict:
    return {"tool_name": "Bash", "session_id": session, "tool_input": {"command": command}}


def test_grep_for_a_known_function_gets_its_definition_and_callers(repo, monkeypatch, capsys):
    text = _hook(monkeypatch, capsys, repo, _grep("leaf"))

    assert text == (
        "MIMRY knows leaf: function at app/one.py:5.\nCalled by helper (app/one.py:1)."
    )


def test_shell_rg_with_two_definitions_gets_both_symbols(repo, monkeypatch, capsys):
    text = _hook(monkeypatch, capsys, repo, _shell('rg -n "def helper|def leaf" app'))

    assert "MIMRY knows helper: function at app/one.py:1." in text
    assert "Called by main (app/one.py:9), helper2 (app/two.py:4)." in text
    assert "MIMRY knows leaf: function at app/one.py:5." in text
    assert text.index("helper:") < text.index("leaf:")
    assert "MIMRY can answer this" not in text


def test_several_definitions_of_one_name_are_listed_sorted(repo, monkeypatch, capsys):
    (repo / "app" / "three.py").write_text("def leaf():\n    return 3\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Second leaf")
    assert _mimry(repo, repo.parent / "cache", "index").returncode == 0

    text = _hook(monkeypatch, capsys, repo, _grep("leaf"))

    assert text.startswith(
        "MIMRY knows leaf: function at app/one.py:5, function at app/three.py:1."
    )


def test_plain_text_gets_todays_reminder_when_broad(repo, monkeypatch, capsys):
    text = _hook(monkeypatch, capsys, repo, _shell('rg "connection|refused" .'))

    assert text.startswith("MIMRY can answer this: run `mimry find")


def test_plain_text_stays_quiet_when_exact(repo, monkeypatch, capsys):
    assert _hook(monkeypatch, capsys, repo, _shell('rg -n "connection refused" .')) == ""


def test_search_of_one_named_file_gets_no_symbol_answer(repo, monkeypatch, capsys):
    assert _hook(monkeypatch, capsys, repo, _grep("leaf", path="app/one.py")) == ""
    assert _hook(monkeypatch, capsys, repo, _shell("rg leaf app/one.py")) == ""
    assert _hook(monkeypatch, capsys, repo, _shell("rg leaf app/one.py 2>/dev/null")) == ""


def test_one_named_file_keeps_todays_reminder_when_broad(repo, monkeypatch, capsys):
    text = _hook(monkeypatch, capsys, repo, _grep("leaf|main", path="app/one.py"))

    assert text.startswith('MIMRY can answer this: run `mimry find "leaf main"`')


def test_symbol_in_a_file_edited_after_indexing_is_skipped(repo, monkeypatch, capsys):
    (repo / "app" / "one.py").write_text(ONE + "\n# edited\n", encoding="utf-8")

    assert _hook(monkeypatch, capsys, repo, _grep("leaf")) == ""
    text = _hook(monkeypatch, capsys, repo, _grep("leaf|main"))
    assert text.startswith("MIMRY can answer this")


def test_graph_over_the_size_limit_shows_definition_and_command(repo, monkeypatch, capsys):
    monkeypatch.setattr(hook, "HOOK_GRAPH_MAX_BYTES", 10)

    text = _hook(monkeypatch, capsys, repo, _grep("leaf"))

    assert text == (
        "MIMRY knows leaf: function at app/one.py:5.\n"
        "Callers are not listed here, the graph is over the hook size limit: "
        "`mimry callers leaf`."
    )


def test_more_callers_than_the_limit_names_the_command(repo, monkeypatch, capsys):
    monkeypatch.setattr(hook, "CALLER_LIMIT", 1)

    text = _hook(monkeypatch, capsys, repo, _grep("helper"))

    assert text == (
        "MIMRY knows helper: function at app/one.py:1.\n"
        "Called by main (app/one.py:9), +1 more: `mimry callers helper`."
    )


def test_plugin_mode_names_the_mcp_tool(repo, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/plugins/mimry")
    monkeypatch.setattr(hook, "CALLER_LIMIT", 1)

    text = _hook(monkeypatch, capsys, repo, _grep("helper"))

    assert text.endswith(
        'Called by main (app/one.py:9), +1 more: call the mimry_symbol tool with name "helper"'
        ' and relations "callers".'
    )


def test_the_same_symbol_twice_in_one_session_prints_once(repo, monkeypatch, capsys):
    first = _hook(monkeypatch, capsys, repo, _grep("leaf", session="same"))
    second = _hook(monkeypatch, capsys, repo, _grep("leaf", session="same"))
    other_session = _hook(monkeypatch, capsys, repo, _grep("leaf", session="other"))

    assert first.startswith("MIMRY knows leaf")
    assert second == ""
    assert other_session.startswith("MIMRY knows leaf")


def test_missing_index_falls_back_to_the_reminder(repo, monkeypatch, capsys):
    (_index(repo) / "mimry.sqlite").unlink()

    assert _hook(monkeypatch, capsys, repo, _grep("leaf")) == ""
    text = _hook(monkeypatch, capsys, repo, _grep("leaf|main"))
    assert text.startswith("MIMRY can answer this")


def test_corrupt_index_falls_back_without_raising(repo, monkeypatch, capsys):
    (_index(repo) / "mimry.sqlite").write_bytes(b"not a sqlite database" * 100)

    assert hook._symbol_answer(repo, "s1", "grep", {"pattern": "leaf"}, "") is None
    text = _hook(monkeypatch, capsys, repo, _grep("leaf|main"))
    assert text.startswith("MIMRY can answer this")


def test_corrupt_graph_falls_back_without_raising(repo, monkeypatch, capsys):
    (_index(repo) / "graph.json").write_text("{not json", encoding="utf-8")

    assert hook._symbol_answer(repo, "s1", "grep", {"pattern": "leaf"}, "") is None
    assert _hook(monkeypatch, capsys, repo, _grep("leaf")) == ""
