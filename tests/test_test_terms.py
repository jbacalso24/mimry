from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from mimry import indexer, search
from mimry.commands import cmd_init
from mimry.indexer import write_index
from mimry.search import find_rows
from mimry.storage import load_pointer


def _index_repo(repo: Path) -> dict:
    assert cmd_init(SimpleNamespace(root=repo, root_type="repo", skip_graph=True)) == 0
    pointer = load_pointer(repo)
    assert pointer is not None
    write_index(repo, pointer)
    refreshed = load_pointer(repo)
    assert refreshed is not None
    return refreshed


def _test_terms(ptr: dict, rel_path: str) -> str:
    con = sqlite3.connect(Path(ptr["indexPath"]) / "mimry.sqlite")
    try:
        row = con.execute(
            "select test_terms from files_fts where rel_path = ?", (rel_path,)
        ).fetchone()
    finally:
        con.close()
    return row[0] or ""


def _cookie_repo(tmp_path: Path, test_source: str) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "cookies.py").write_text("def refresh(jar):\n    return jar\n")
    (repo / "tests" / "test_cookies.py").write_text(test_source)
    return repo


SESSION_TEST = (
    "from src.cookies import refresh\n\n\n"
    "def test_session_cookie_renewed_on_each_request():\n"
    "    refresh(None)\n"
)


def test_test_name_words_reach_the_file_under_test(tmp_path):
    ptr = _index_repo(_cookie_repo(tmp_path, SESSION_TEST))

    assert _test_terms(ptr, "src/cookies.py") == "cookie each renewed request session"


def test_test_name_words_rank_the_file_under_test(tmp_path):
    ptr = _index_repo(_cookie_repo(tmp_path, SESSION_TEST))

    rows = find_rows(Path(ptr["indexPath"]), "session cookie renewed", limit=10)

    assert "src/cookies.py" in [row["path"] for row in rows]


def test_zero_weight_writes_no_test_terms_and_scores_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(indexer, "TEST_TERMS_WEIGHT", 0)
    monkeypatch.setattr(search, "TEST_TERMS_WEIGHT", 0)
    ptr = _index_repo(_cookie_repo(tmp_path, SESSION_TEST))

    assert _test_terms(ptr, "src/cookies.py") == ""
    rows = find_rows(Path(ptr["indexPath"]), "session cookie renewed", limit=10)
    assert "src/cookies.py" in [row["path"] for row in rows]


def test_stopwords_and_short_words_are_dropped(tmp_path):
    test_source = (
        "from src.cookies import refresh\n\n\n"
        "def test_it_should_on_session_renewed():\n"
        "    refresh(None)\n"
    )
    ptr = _index_repo(_cookie_repo(tmp_path, test_source))

    assert set(_test_terms(ptr, "src/cookies.py").split()) == {"session", "renewed"}


def test_terms_are_capped_most_frequent_first(tmp_path):
    cases = "".join(f"def test_renewal_w{i:04d}():\n    refresh(None)\n\n\n" for i in range(350))
    ptr = _index_repo(_cookie_repo(tmp_path, "from src.cookies import refresh\n\n\n" + cases))

    words = _test_terms(ptr, "src/cookies.py").split()

    assert len(words) == indexer.TEST_TERMS_CAP == 300
    assert words[0] == "renewal"
    assert "w0000" in words
    assert "w0349" not in words


def test_only_the_direct_target_of_a_test_gets_its_words(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "rotate.py").write_text("def rotate_token(jar):\n    return jar\n")
    (repo / "src" / "cookies.py").write_text(
        "from src.rotate import rotate_token\n\n\n"
        "def refresh(jar):\n    return rotate_token(jar)\n"
    )
    (repo / "tests" / "test_cookies.py").write_text(
        "from src.cookies import refresh\n\n\n"
        "def test_session_cookie_renewed_on_each_request():\n"
        "    refresh(None)\n"
    )

    ptr = _index_repo(repo)

    assert _test_terms(ptr, "src/cookies.py") != ""
    assert _test_terms(ptr, "src/rotate.py") == ""


def test_imported_file_gets_the_words_of_every_test_in_the_file(tmp_path):
    test_source = (
        "from src.cookies import refresh\n\n\ndef test_session_renewed():\n    assert True\n"
    )
    ptr = _index_repo(_cookie_repo(tmp_path, test_source))

    assert set(_test_terms(ptr, "src/cookies.py").split()) == {"session", "renewed"}


def test_non_test_symbols_in_a_test_file_contribute_nothing(tmp_path):
    test_source = (
        "from src.cookies import refresh\n\n\ndef helper_renewal_fixture():\n    refresh(None)\n"
    )
    ptr = _index_repo(_cookie_repo(tmp_path, test_source))

    assert _test_terms(ptr, "src/cookies.py") == ""


def test_test_files_never_receive_test_terms(tmp_path):
    ptr = _index_repo(_cookie_repo(tmp_path, SESSION_TEST))

    assert _test_terms(ptr, "tests/test_cookies.py") == ""


@pytest.mark.parametrize("name", ["test_a", "TestCookies", "CookieTest", "cookie_test"])
def test_test_name_patterns(name):
    assert indexer._is_test_name(name)


@pytest.mark.parametrize("name", ["refresh", "testimony", "contest"])
def test_non_test_names_are_not_test_names(name):
    assert not indexer._is_test_name(name)
