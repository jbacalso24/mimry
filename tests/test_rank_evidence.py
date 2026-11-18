"""Each ranked file says what it matched, strongest first (#647).

The evidence is read from the index the search already holds. These
tests check the text, the Why line's caps, and that the ranking itself
does not move when the evidence is added.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mimry import search
from mimry.indexer import write_index
from mimry.lifecycle import run_init
from mimry.mcp_tools import mimry_find, mimry_preflight
from mimry.queries import WHY_CHARS, WHY_ITEMS, _why_line
from mimry.search import _row_evidence, find_rows
from mimry.storage import load_pointer

SESSION_STORE = """\
def load_session(key):
    return read_token(key)


def save_session(key, value):
    write_token(key, value)


def read_token(key):
    return key


def write_token(key, value):
    return value
"""
HOOK = """\
from session_store import load_session


def on_start(key):
    return load_session(key)
"""
BANNER = "def render_banner(text):\n    return text.upper()\n"
QUERIES = [
    "session store",
    "token",
    "session",
    "where is `load_session` defined",
]


@pytest.fixture
def indexed(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "session_store.py").write_text(SESSION_STORE, encoding="utf-8")
    (repo / "src" / "hook.py").write_text(HOOK, encoding="utf-8")
    (repo / "src" / "banner.py").write_text(BANNER, encoding="utf-8")
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("MIMRY_NO_GLOBAL_GITIGNORE", "1")
    run_init(
        SimpleNamespace(
            root=str(repo),
            root_type="repo",
            skip_graph=False,
            quiet=True,
            no_global_gitignore=True,
        )
    )
    write_index(repo, load_pointer(repo))
    return repo, load_pointer(repo)


def _rows(repo: Path, ptr: dict, query: str) -> list[dict]:
    return find_rows(Path(ptr["indexPath"]), query, 10, True, root=repo, root_id=ptr.get("rootId"))


def _row(rows: list[dict], path: str) -> dict:
    return next(row for row in rows if row["path"] == path)


def test_a_file_found_by_name_says_which_terms_are_in_its_name(indexed):
    repo, ptr = indexed

    rows = _rows(repo, ptr, "session store")

    assert 'file name has "session", "store"' in _row(rows, "src/session_store.py")["evidence"]


def test_a_file_that_defines_a_named_identifier_says_so_first(indexed):
    repo, ptr = indexed

    rows = _rows(repo, ptr, "where is `load_session` defined")

    assert _row(rows, "src/session_store.py")["evidence"][0] == "defines `load_session`"


def test_content_evidence_names_the_term_and_how_often(indexed):
    repo, ptr = indexed

    rows = _rows(repo, ptr, "token")

    # Four functions in session_store.py hold the word "token".
    assert '"token" in 4 places' in _row(rows, "src/session_store.py")["evidence"]


def test_a_graph_connected_file_names_the_connected_result(indexed):
    repo, ptr = indexed

    rows = _rows(repo, ptr, "session store")

    assert "imports `src/session_store.py`" in _row(rows, "src/hook.py")["evidence"]


def test_matched_symbols_are_named_unless_the_defines_item_names_them():
    row = {
        "path": "src/x.py",
        "reason": "graph node label match; nodes: a_fn, b_fn, c_fn, d_fn",
    }

    named = _row_evidence(row, {}, [], set(), {})
    assert ("has `a_fn`, `b_fn`, `c_fn`") in [text for _, text in named]

    defined = {"a_fn", "b_fn", "c_fn", "d_fn"}
    texts = [text for _, text in _row_evidence(row, {}, [], defined, {})]
    assert not any(text.startswith("has ") for text in texts)


def test_every_row_carries_an_evidence_list(indexed):
    repo, ptr = indexed

    for query in QUERIES:
        for row in _rows(repo, ptr, query):
            assert isinstance(row["evidence"], list)
            assert all(isinstance(item, str) and item for item in row["evidence"])


def test_evidence_does_not_change_order_scores_or_reasons(indexed, monkeypatch):
    repo, ptr = indexed

    def without_evidence(rows, *args):
        return [{key: value for key, value in row.items() if key != "evidence"} for row in rows]

    with_evidence = {query: _rows(repo, ptr, query) for query in QUERIES}
    monkeypatch.setattr(search, "_with_evidence", without_evidence)
    for query in QUERIES:
        before = [(r["path"], r["score"], r["reason"]) for r in with_evidence[query]]
        after = [(r["path"], r["score"], r["reason"]) for r in _rows(repo, ptr, query)]
        assert after == before, query


def test_the_why_line_keeps_at_most_four_items():
    items = [f'file name has "t{n}"' for n in range(6)]

    line = _why_line({"path": "a.py", "reason": "", "evidence": items})

    assert line == "Why: " + "; ".join(items[:WHY_ITEMS])


def test_the_why_line_stops_before_220_characters():
    items = [f'file name has "term{n}{"x" * 40}"' for n in range(6)]

    line = _why_line({"path": "a.py", "reason": "", "evidence": items})

    assert line.startswith("Why: file name has")
    assert len(line) <= WHY_CHARS
    assert line.count("; ") < WHY_ITEMS - 1


def test_a_single_long_item_is_cut_to_the_character_cap():
    line = _why_line({"path": "a.py", "reason": "", "evidence": ["x" * 400]})

    assert len(line) == WHY_CHARS
    assert line.endswith("...")


def test_a_row_without_evidence_keeps_the_older_phrases():
    line = _why_line(
        {
            "path": "a.py",
            "reason": "filename match, token match: session",
            "evidence": [],
        }
    )

    assert line == "Why: name matches; matched: session"


def test_mcp_find_and_preflight_carry_the_evidence(indexed):
    repo, _ptr = indexed

    found = mimry_find("session store", str(repo))
    assert "error" not in found, found
    hit = next(row for row in found["results"] if row["path"] == "src/session_store.py")
    assert 'file name has "session", "store"' in hit["evidence"]
    assert {"path", "score", "reason", "parts"} <= set(hit)

    preflight = mimry_preflight("session store", str(repo))
    assert preflight.get("returncode", 0) == 0, preflight
    listed = {row["path"]: row for row in preflight["results"]}
    assert 'file name has "session", "store"' in listed["src/session_store.py"]["evidence"]
