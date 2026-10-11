from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from mimry.commands import _write_context_pack, cmd_init
from mimry.indexer import write_index
from mimry.paths import context_file
from mimry.pieces import best_ranges, file_pieces, piece_matches
from mimry.search import find_rows
from mimry.storage import load_pointer

# Low-entropy canary in GitHub token shape, built from fragments so
# secret scanners do not flag the test itself.
TOKEN_TAIL = "FAKE" + "CANARY" + ("7" * 26)


def _index_repo(repo: Path) -> dict:
    assert cmd_init(SimpleNamespace(root=repo, root_type="repo", skip_graph=True)) == 0
    write_index(repo, load_pointer(repo))
    return load_pointer(repo)


def _large_module(match_at: int) -> str:
    """Many unrelated functions, and one about invoice tax."""
    parts = [f"def helper_{i}(value):\n    return value + {i}\n" for i in range(match_at)]
    parts.append(
        "def calculate_invoice_tax(subtotal, rate):\n"
        "    tax = subtotal * rate\n"
        "    return round(tax, 2)\n"
    )
    parts += [f"def other_{i}(value):\n    return value - {i}\n" for i in range(40)]
    return "\n".join(parts)


def test_file_pieces_span_symbols_and_skip_unusable_ones():
    data = b"def a():\n    pass\n\ndef b():\n    return 1\n"
    symbols = [
        {"name": "a", "line_start": 1, "line_end": 2},
        {"name": "b", "line_start": 4, "line_end": 99},
        {"name": "no_end", "line_start": 4},
        {"name": "backwards", "line_start": 5, "line_end": 4},
        {"name": "past_end", "line_start": 50, "line_end": 60},
    ]
    pieces = file_pieces(data, symbols)
    assert [(start, end, name) for start, end, name, _body in pieces] == [(1, 2, "a"), (4, 5, "b")]


def test_best_ranges_skip_overlaps_and_keep_file_order():
    matches = [(-9.0, 40, 60, "method"), (-8.0, 30, 90, "klass"), (-7.0, 5, 10, "early")]
    assert best_ranges(matches) == [
        {"start": 5, "end": 10, "name": "early"},
        {"start": 40, "end": 60, "name": "method"},
    ]


def test_find_names_the_matching_function_lines_in_a_large_file(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "billing.py").write_text(_large_module(match_at=60), encoding="utf-8")
    ptr = _index_repo(repo)

    rows = find_rows(Path(ptr["indexPath"]), "change how invoice tax is calculated", limit=3)

    assert rows[0]["path"] == "src/billing.py"
    assert rows[0]["ranges"][0] == {"start": 181, "end": 183, "name": "calculate_invoice_tax"}


def test_context_pack_lists_the_lines_to_read_first(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "billing.py").write_text(_large_module(match_at=3), encoding="utf-8")
    ptr = _index_repo(repo)

    _write_context_pack(repo, ptr, "change how invoice tax is calculated")

    pack = context_file(repo).read_text(encoding="utf-8")
    assert "Read first: lines 10-12 (`calculate_invoice_tax`)" in pack


def test_an_index_without_pieces_falls_back_to_whole_files(tmp_path):
    idx = tmp_path / "idx"
    idx.mkdir()
    sqlite3.connect(idx / "mimry.sqlite").close()
    assert piece_matches(idx, "invoice tax") == {}


def test_credentials_in_a_function_never_reach_the_pieces_table(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    value = "gh" + "p_" + TOKEN_TAIL
    (repo / "client.py").write_text(
        f'def connect():\n    token = "{value}"\n    return token\n', encoding="utf-8"
    )
    (repo / "ping.py").write_text("def ping():\n    return 'pong'\n", encoding="utf-8")
    ptr = _index_repo(repo)

    con = sqlite3.connect(Path(ptr["indexPath"]) / "mimry.sqlite")
    try:
        stored = con.execute("select name, body from pieces_fts").fetchall()
    finally:
        con.close()
    assert "ping" in [name for name, _body in stored]
    for name, body in stored:
        assert TOKEN_TAIL.lower() not in f"{name} {body}".lower()
