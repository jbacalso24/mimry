from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mimry import intent
from mimry.commands import cmd_init
from mimry.core.artifacts import _term_weights
from mimry.indexer import write_index
from mimry.intent import light_stem
from mimry.search import _fts_match_query, find_rows
from mimry.storage import load_pointer


@pytest.mark.parametrize(
    ("word", "stem"),
    [
        # ies -> y
        ("stories", "story"),
        ("queries", "query"),
        # sses -> ss, and es after s, x, z, ch, sh
        ("classes", "class"),
        ("addresses", "address"),
        ("patches", "patch"),
        ("indexes", "index"),
        # s, except after s, u or i
        ("errors", "error"),
        ("pages", "page"),
        ("cases", "case"),
        ("analysis", "analysis"),
        ("status", "status"),
        # ing and ed, with a doubled final consonant undoubled
        ("stopping", "stop"),
        ("dropped", "drop"),
        ("indexed", "index"),
        ("callbacks", "callback"),
        ("stopped", "stop"),
        # l, s and z stay doubled
        ("called", "call"),
        ("passed", "pass"),
        ("buzzing", "buzz"),
        # the stem must keep at least four letters
        ("running", "running"),
        ("using", "using"),
        ("used", "used"),
        ("cats", "cats"),
        ("runs", "runs"),
        ("ties", "ties"),
        # exceptions are not stripped
        ("canvas", "canvas"),
        ("axis", "axis"),
        ("series", "series"),
        ("news", "news"),
        ("this", "this"),
        ("does", "does"),
        # words that are already stems
        ("callback", "callback"),
        ("teardown", "teardown"),
        ("tests", "test"),
    ],
)
def test_light_stem_table(word, stem):
    assert light_stem(word) == stem


def test_light_stem_leaves_non_ascii_words_alone():
    assert light_stem("na\u00efves") == "na\u00efves"
    assert light_stem("\u00e9v\u00e9nements") == "\u00e9v\u00e9nements"


def test_fts_clause_adds_stem_prefix_when_on():
    assert _fts_match_query(["callbacks"]) == '("callbacks"* OR "callback"*)'
    assert _fts_match_query(["called"]) == '("called"* OR "call"*)'
    assert _fts_match_query(["teardown"]) == '"teardown"*'


def test_fts_clause_is_as_written_when_off(monkeypatch):
    monkeypatch.setattr(intent, "_QUERY_STEMS", False)
    assert _fts_match_query(["callbacks"]) == '"callbacks"*'
    assert _fts_match_query(["called"]) == '"called"*'


def _index_repo(repo: Path) -> Path:
    assert cmd_init(SimpleNamespace(root=repo, root_type="repo", skip_graph=True)) == 0
    pointer = load_pointer(repo)
    assert pointer is not None
    write_index(repo, pointer)
    refreshed = load_pointer(repo)
    assert refreshed is not None
    return Path(refreshed["indexPath"])


def test_callbacks_query_finds_callback_module(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "src" / "hooks").mkdir(parents=True)
    (repo / "src" / "hooks" / "callback.py").write_text(
        "def register_callback(fn):\n    _handlers.append(fn)\n", encoding="utf-8"
    )
    (repo / "src" / "app.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    idx = _index_repo(repo)

    rows = find_rows(idx, "callbacks", limit=10)
    assert rows[0]["path"] == "src/hooks/callback.py"

    monkeypatch.setattr(intent, "_QUERY_STEMS", False)
    assert "src/hooks/callback.py" not in [row["path"] for row in find_rows(idx, "callbacks")]


def test_error_and_errors_share_one_rarity_weight(monkeypatch):
    file_hits = {
        "a.py": (frozenset({"error"}), []),
        "b.py": (frozenset(), [(frozenset({"error"}), {})]),
    }
    weights = _term_weights(["error", "errors"], 10, file_hits)
    assert weights["error"] == weights["errors"]

    monkeypatch.setattr(intent, "_QUERY_STEMS", False)
    weights = _term_weights(["error", "errors"], 10, file_hits)
    assert weights["error"] != weights["errors"]
