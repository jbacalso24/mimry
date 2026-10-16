from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from mimry.commands import cmd_init
from mimry.indexer import write_index
from mimry.paths import context_file
from mimry.pieces import best_ranges, file_pieces, piece_matches
from mimry.queries import write_context_pack
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


def test_a_class_and_its_first_method_keep_separate_pieces():
    """The method starts on the line after ``class``. Its piece must not
    be dropped as a duplicate of the class's range once attached lines
    are taken in."""
    data = b"class Repo:\n    def extend(self):\n        return 1\n"
    symbols = [
        {"name": "Repo", "line_start": 1, "line_end": 3},
        {"name": "extend", "line_start": 2, "line_end": 3},
    ]
    pieces = file_pieces(data, symbols)
    assert [name for _start, _end, name, _body in pieces] == ["Repo", "extend"]


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

    write_context_pack(repo, ptr, "change how invoice tax is calculated")

    pack = context_file(repo).read_text(encoding="utf-8")
    assert "Read first: lines 10-12 (`calculate_invoice_tax`)" in pack


def test_an_index_without_pieces_falls_back_to_whole_files(tmp_path):
    idx = tmp_path / "idx"
    idx.mkdir()
    sqlite3.connect(idx / "mimry.sqlite").close()
    assert piece_matches(idx, "invoice tax", ["any-file"]) == {}


def test_code_above_a_symbol_is_not_attached_but_comments_and_decorators_are():
    """Only comments, decorators and attributes join a piece.

    A code line above a symbol belongs to another symbol. Taking it in
    hid the enclosing function behind a stray piece that starts on a
    signature.
    """
    data = (
        b"import os\nvalue = os.sep\ndef f():\n    return value\n"
        b"# doc\n@cached\ndef g():\n    return 1\n"
    )
    symbols = [
        {"name": "f", "line_start": 3, "line_end": 4},
        {"name": "g", "line_start": 7, "line_end": 8},
    ]
    pieces = file_pieces(data, symbols)
    assert [(start, end, name) for start, end, name, _body in pieces] == [
        (3, 4, "f"),
        (5, 8, "g"),
    ]


def test_a_result_file_keeps_its_piece_outside_the_global_top_n(tmp_path):
    """A result keeps its piece even when the repository cap cuts it.

    Other files with better-ranked pieces for the same terms can push a
    result's own piece past the 400 best in the repository. The lines to
    read come from the result files, so the piece must still be found.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    for n in range(3):
        decoy = "".join(
            f"def invoice_tax_{n}_{i}(subtotal, rate):\n    return round(subtotal * rate, 2)\n\n"
            for i in range(150)
        )
        (repo / f"decoy_{n}.py").write_text(decoy, encoding="utf-8")
    target = (
        "def settle(account, amount):\n"
        + "    ledger = account.ledger\n" * 20
        + "    return invoice_for(amount)  # tax on the invoice\n"
    )
    (repo / "settle.py").write_text(target, encoding="utf-8")
    ptr = _index_repo(repo)
    idx = Path(ptr["indexPath"])
    con = sqlite3.connect(idx / "mimry.sqlite")
    try:
        target_id = con.execute("select file_id from pieces_fts where name = 'settle'").fetchone()[
            0
        ]
        matching = con.execute(
            "select count(*) from pieces_fts where pieces_fts match ?",
            ('"invoice"* OR "tax"*',),
        ).fetchone()[0]
    finally:
        con.close()
    assert matching > 400

    matches = piece_matches(idx, "invoice tax", [target_id])

    assert "settle" in [name for _rank, _start, _end, name in matches[target_id]]


def test_preflight_and_find_name_the_same_ranges_for_the_same_text(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "billing.py").write_text(_large_module(match_at=60), encoding="utf-8")
    ptr = _index_repo(repo)
    idx = Path(ptr["indexPath"])
    task = "change how invoice tax is calculated"

    preflight = find_rows(idx, task, 8, True, root=repo, root_id=ptr.get("rootId"))
    found = find_rows(idx, task, 10)
    write_context_pack(repo, ptr, task)

    def ranges(rows):
        return next(r["ranges"] for r in rows if r["path"] == "src/billing.py")

    expected = [{"start": 181, "end": 183, "name": "calculate_invoice_tax"}]
    assert ranges(preflight) == ranges(found) == expected
    pack = context_file(repo).read_text(encoding="utf-8")
    assert "Read first: lines 181-183 (`calculate_invoice_tax`)" in pack


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


def test_symbols_sharing_one_line_share_one_piece(monkeypatch):
    """A minified bundle puts thousands of functions on one line. Each
    used to tokenize that whole line again, which is quadratic."""
    import mimry.pieces as pieces_module

    calls = []
    real_body_terms = pieces_module.body_terms

    def counting(data):
        calls.append(len(data))
        return real_body_terms(data)

    monkeypatch.setattr(pieces_module, "body_terms", counting)
    data = "".join(f"function f{i}(){{return {i}}};" for i in range(3000)).encode() + b"\n"
    symbols = [{"name": f"f{i}", "line_start": 1, "line_end": 1} for i in range(3000)]

    pieces = file_pieces(data, symbols)

    assert [(start, end) for start, end, _name, _body in pieces] == [(1, 1)]
    assert len(calls) == 1


def test_a_bracket_attribute_joins_the_piece_but_a_list_line_does_not():
    data = b"[Serializable]\nclass A:\n    pass\nitems = [\n]\ndef f():\n    pass\n"
    symbols = [
        {"name": "A", "line_start": 2, "line_end": 3},
        {"name": "f", "line_start": 6, "line_end": 7},
    ]
    pieces = file_pieces(data, symbols)
    assert [(start, end, name) for start, end, name, _body in pieces] == [
        (1, 3, "A"),
        (6, 7, "f"),
    ]
