from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from mimry.commands import cmd_init
from mimry.indexer import write_index
from mimry.intent import TOKEN_RE, cjk_bigrams, query_terms
from mimry.search import _fts_match_query, find_rows
from mimry.storage import load_pointer


def _index_repo(repo: Path) -> Path:
    assert cmd_init(SimpleNamespace(root=repo, root_type="repo", skip_graph=True)) == 0
    pointer = load_pointer(repo)
    assert pointer is not None
    write_index(repo, pointer)
    refreshed = load_pointer(repo)
    assert refreshed is not None
    return Path(refreshed["indexPath"])


def _paths(idx: Path, query: str) -> list[str]:
    return [row["path"] for row in find_rows(idx, query, limit=10)]


def _cjk_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "日本語").mkdir(parents=True)
    (repo / "日本語" / "漢字.py").write_text("def 日本語関数():\n    return 1\n", encoding="utf-8")
    # The CJK text sits past the 40-line content hint, so only the
    # full-file FTS body can match it.
    filler = "\n".join(f"x{i} = {i}" for i in range(1, 46))
    (repo / "deep.py").write_text(f'{filler}\ny = "処理結果を保存する"\n', encoding="utf-8")
    (repo / "korean.py").write_text(
        f"{filler}\nname = '한국어 처리 결과'\nlabel = 'カタカナ'\n", encoding="utf-8"
    )
    return repo


def test_issue_commands_find_cjk_file_name_and_content(tmp_path):
    """The two commands from #277 find 日本語/漢字.py."""
    idx = _index_repo(_cjk_repo(tmp_path))

    assert _paths(idx, "日本語関数")[0] == "日本語/漢字.py"
    assert _paths(idx, "漢字")[0] == "日本語/漢字.py"


def test_two_character_substring_of_a_longer_run_is_found(tmp_path):
    """A two-character piece of a longer CJK run finds the file."""
    idx = _index_repo(_cjk_repo(tmp_path))

    assert "日本語/漢字.py" in _paths(idx, "本語")
    # Substring of a run that only the deep body contains.
    assert _paths(idx, "処理結")[0] == "deep.py"
    assert _paths(idx, "結果を")[0] == "deep.py"


def test_hangul_and_katakana_runs_are_found(tmp_path):
    idx = _index_repo(_cjk_repo(tmp_path))

    assert _paths(idx, "한국어")[0] == "korean.py"
    assert _paths(idx, "국어")[0] == "korean.py"
    assert _paths(idx, "カタカナ")[0] == "korean.py"
    assert _paths(idx, "タカ")[0] == "korean.py"


def test_ascii_query_terms_and_match_string_are_unchanged():
    assert query_terms("Fix get_user-Token2 in parser") == [
        "fix",
        "get",
        "user",
        "token2",
        "parser",
    ]
    assert TOKEN_RE.findall("get_user-Token2") == ["get", "user", "Token2"]
    # Accented Latin still splits as before (CJK ranges, not \w).
    assert query_terms("fooé bar") == ["foo", "bar"]
    assert _fts_match_query(["login", "session"]) == '"login"* OR "session"*'


def test_cjk_run_splits_from_ascii_and_bigrams_pair_up():
    assert TOKEN_RE.findall("foo日本語bar") == ["foo", "日本語", "bar"]
    assert cjk_bigrams("日本語") == ["日本", "本語"]
    assert cjk_bigrams("漢字") == ["漢字"]
    assert cjk_bigrams("字") == ["字"]
