"""The hook answers a broad search with the files the index ranks first
for it, next to the search, which still runs (#656). The reminder is the
fallback for a repository past the prompt hook's size gate, a query that
no file matches, or a failure."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from test_hook_symbols import _git, _grep, _hook, _mimry, _ranked_head, _shell

from mimry import hook
from mimry.hook import PROMPT_HEAD, PROMPT_NOTE_LIMIT, _prompt_note
from mimry.session import QUERY_LOG

BILLING = """def refund_invoice(invoice_id, amount):
    \"\"\"Refund part of an invoice to the customer.\"\"\"
    return {"invoice": invoice_id, "refunded": amount}


def ledger_total(entries):
    return sum(entries)
"""

AUTH = """def login(user, password):
    session_token = make_token(user)
    return session_token


def expire_session(token):
    return token is None
"""

SHOP = "def add_to_cart(item):\n    return [item]\n"


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    root = tmp_path_factory.mktemp("answer") / "repo"
    cache = root.parent / "cache"
    (root / "app").mkdir(parents=True)
    (root / "app" / "billing.py").write_text(BILLING, encoding="utf-8")
    (root / "app" / "auth.py").write_text(AUTH, encoding="utf-8")
    (root / "app" / "shop.py").write_text(SHOP, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "MIMRY Test")
    _git(root, "config", "user.email", "test@example.com")
    assert _mimry(root, cache, "init").returncode == 0
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "Initial commit")
    assert _mimry(root, cache, "index").returncode == 0
    return root


@pytest.fixture(autouse=True)
def _no_plugin_or_feedback_env(monkeypatch):
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.delenv("MIMRY_SESSION_FEEDBACK", raising=False)


def _query_rows(repo: Path) -> list[dict]:
    path = repo / ".mimry" / QUERY_LOG
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_broad_grep_gets_the_ranked_files_with_their_lines(repo, monkeypatch, capsys):
    text = _hook(monkeypatch, capsys, repo, _grep("invoice.*refund", session="rank"))

    assert text.startswith(_ranked_head("invoice refund"))
    first_file = text.splitlines()[1]
    assert re.fullmatch(r"- app/billing\.py: lines \d+-\d+.*", first_file), first_file
    assert text.splitlines()[-1] == 'Run `mimry preflight "<task>"` for the full context pack.'


def test_the_same_broad_query_twice_in_a_session_is_answered_once(repo, monkeypatch, capsys):
    first = _hook(monkeypatch, capsys, repo, _grep("invoice.*refund", session="dedupe"))
    second = _hook(monkeypatch, capsys, repo, _grep("invoice.*refund", session="dedupe"))

    assert first.startswith(_ranked_head("invoice refund"))
    assert second == ""


def test_four_different_broad_queries_in_one_session_are_all_answered(repo, monkeypatch, capsys):
    queries = ["invoice.*refund", "login.*token", "expire.*session", "ledger.*total"]
    texts = [_hook(monkeypatch, capsys, repo, _grep(query, session="no-cap")) for query in queries]

    assert all(text.startswith("MIMRY ranked these files for") for text in texts), texts
    assert texts[1].splitlines()[1].startswith("- app/auth.py")
    assert texts[3].splitlines()[1].startswith("- app/billing.py")


def test_shell_case_insensitive_search_of_the_tree_gets_the_ranked_files(
    repo, monkeypatch, capsys
):
    text = _hook(monkeypatch, capsys, repo, _shell("rg -i invoice.*refund app", session="sh"))

    assert text.startswith(_ranked_head("invoice refund"))
    assert text.splitlines()[1].startswith("- app/billing.py")


def test_bare_exact_literal_stays_quiet_by_design(repo, monkeypatch, capsys):
    # broad_search_query takes only regex or case-insensitive searches,
    # so an exact literal names nothing to rank (as before #656).
    assert _hook(monkeypatch, capsys, repo, _shell("rg invoice_id app", session="lit")) == ""


def test_a_repo_over_the_prompt_size_gate_gets_the_reminder(repo, monkeypatch, capsys):
    monkeypatch.setattr(hook, "PROMPT_MAX_FILES", 1)

    text = _hook(monkeypatch, capsys, repo, _grep("invoice.*refund", session="gate"))

    assert text.startswith('MIMRY can answer this: run `mimry find "invoice refund"`')


def test_a_query_no_file_matches_gets_the_reminder(repo, monkeypatch, capsys):
    text = _hook(monkeypatch, capsys, repo, _grep("zebra.*quantum", session="nomatch"))

    assert text.startswith('MIMRY can answer this: run `mimry find "zebra quantum"`')


def test_a_failing_ranking_falls_back_to_the_reminder(repo, monkeypatch, capsys):
    import mimry.search

    def broken(*args, **kwargs):
        raise RuntimeError("index unreadable")

    monkeypatch.setattr(mimry.search, "find_rows", broken)

    text = _hook(monkeypatch, capsys, repo, _grep("invoice.*refund", session="boom"))

    assert text.startswith("MIMRY can answer this: run `mimry find")


def test_the_answer_is_recorded_as_a_query_with_the_files_shown(repo, monkeypatch, capsys):
    _hook(monkeypatch, capsys, repo, _grep("ledger.*total", session="logged"))

    row = [r for r in _query_rows(repo) if r.get("session") == "logged"][-1]
    assert "app/billing.py" in row["shown"]


def test_plugin_mode_names_the_preflight_tool(repo, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/plugins/mimry")

    text = _hook(monkeypatch, capsys, repo, _grep("add.*cart", session="plugin"))

    assert text.startswith(_ranked_head("add cart"))
    assert text.splitlines()[-1] == (
        "Call the mimry_preflight tool with the task for the full context pack."
    )


def test_prompt_note_keeps_its_head_and_foot_by_default():
    rows = [{"path": "src/a.py", "score": 3, "ranges": [{"start": 1, "end": 2}]}]

    note = _prompt_note(rows)

    assert note is not None
    assert note.splitlines()[0] == PROMPT_HEAD
    assert note.splitlines()[-1] == 'Run `mimry preflight "<task>"` for the full context pack.'
    assert len(note) <= PROMPT_NOTE_LIMIT
