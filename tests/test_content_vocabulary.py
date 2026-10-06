from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from mimry.commands import cmd_init
from mimry.indexer import write_index
from mimry.scanner import body_terms
from mimry.search import find_rows
from mimry.storage import connect, load_pointer


@pytest.fixture
def tmp_idx(tmp_path):
    """Create a temporary index directory with initialized database."""
    idx = tmp_path / "idx"
    idx.mkdir(parents=True, exist_ok=True)
    con = connect(idx)
    con.close()
    return idx


def _index_repo(repo: Path) -> dict:
    """Initialize and index a repository."""
    assert cmd_init(SimpleNamespace(root=repo, root_type="repo", skip_graph=True)) == 0
    pointer = load_pointer(repo)
    assert pointer is not None
    write_index(repo, pointer)
    refreshed = load_pointer(repo)
    assert refreshed is not None
    return refreshed


def test_deep_word_in_python_file_is_found_by_find_rows(tmp_path):
    """A distinctive word after 70 filler lines is found by find_rows.

    The word 'partition' appears only deep in the file, well beyond
    content_hint's 40-line limit, and must be discoverable via
    body_terms.
    """
    repo = tmp_path / "repo"
    repo.mkdir()

    # Create a Python file with 'partition' only after 70 filler lines
    filler = "\n".join(f"# Line {i}: filler content" for i in range(1, 71))
    py_file = repo / "deep.py"
    py_file.write_text(f"{filler}\ndef use_partition():\n    return x.partition('/')\n")

    ptr = _index_repo(repo)

    # find_rows should return the file for "partition"
    rows = find_rows(Path(ptr["indexPath"]), "partition", limit=10)
    paths = [r["path"] for r in rows]

    assert any("deep.py" in p for p in paths), f"deep.py not found in {paths}"


def test_deep_camelcase_token_is_found_by_find_rows(tmp_path):
    """A token from camelCase deep in a file is found by find_rows.

    The function getUserToken() appears deep in the file, and 'token'
    should be found via body_terms.
    """
    repo = tmp_path / "repo"
    repo.mkdir()

    js_file = repo / "api.js"
    filler = "\n".join(f"// Line {i}" for i in range(1, 50))
    js_file.write_text(
        f"{filler}\nfunction refreshAuth() {{\n"
        f"  const token = getUserToken();\n"
        f"  return token;\n}}\n"
    )

    ptr = _index_repo(repo)

    rows = find_rows(Path(ptr["indexPath"]), "token", limit=10)
    paths = [r["path"] for r in rows]

    assert any("api.js" in p for p in paths), f"api.js not found in {paths}"


def test_repeated_words_score_higher_than_unique_words(tmp_path):
    """Files with repeated distinctive words score higher for that term.

    Two identical files except one uses 'database' three times and the
    other once should score higher for the 'database' query.
    """
    repo = tmp_path / "repo"
    repo.mkdir()

    (repo / "sparse.py").write_text("def query():\n    db = Database()\n    return db.execute()\n")

    (repo / "dense.py").write_text(
        "class Database:\n    pass\ndef use_database():\n    db = Database()\n    db.query()\n"
    )

    ptr = _index_repo(repo)
    idx = Path(ptr["indexPath"])

    rows = find_rows(idx, "database", limit=10)
    scores = {r["path"]: r["score"] for r in rows}

    sparse_score = [s for p, s in scores.items() if "sparse.py" in p]
    dense_score = [s for p, s in scores.items() if "dense.py" in p]

    assert sparse_score, "sparse.py not found"
    assert dense_score, "dense.py not found"
    assert dense_score[0] > sparse_score[0], (
        f"dense.py ({dense_score[0]}) should score higher than sparse.py ({sparse_score[0]})"
    )


def test_incremental_reuse_replays_deep_body_terms(tmp_path):
    """Body terms are replayed from adapt cache on file reuse.

    Index a repo, change a different file, reindex; the unchanged
    file's deep word should still be found.
    """
    repo = tmp_path / "repo"
    repo.mkdir()

    stable_py = repo / "stable.py"
    stable_py.write_text(
        "\n".join(f"# Line {i}" for i in range(1, 50))
        + "\ndef far_away_function():\n    return x.partition(y)\n"
    )
    changing_py = repo / "changing.py"
    changing_py.write_text("# Initial content\n")

    ptr1 = _index_repo(repo)
    rows1 = find_rows(Path(ptr1["indexPath"]), "partition", limit=10)
    paths1 = [r["path"] for r in rows1]
    assert any("stable.py" in p for p in paths1)

    changing_py.write_text("# Modified content\n# More changes\n")

    # Reindex: stable.py should be reused and its deep word still found
    # by write_index which automatically uses the previous generation's
    # adapt cache for unchanged files.
    pointer2 = load_pointer(repo)
    assert pointer2 is not None
    write_index(repo, pointer2)
    ptr2 = load_pointer(repo)
    assert ptr2 is not None

    rows2 = find_rows(Path(ptr2["indexPath"]), "partition", limit=10)
    paths2 = [r["path"] for r in rows2]
    assert any("stable.py" in p for p in paths2)


# Low-entropy canary in GitHub token shape, built from fragments so
# secret scanners do not flag the test itself.
GITHUB_TOKEN_TAIL = "FAKE" + "CANARY" + ("7" * 26)


def test_body_terms_drops_redacted_credentials():
    value = "gh" + "p_" + GITHUB_TOKEN_TAIL
    assert GITHUB_TOKEN_TAIL.lower() not in body_terms(f'token = "{value}"\n'.encode())


def test_credential_deep_in_file_never_reaches_the_fts_table(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    filler = "\n".join(f"# Line {i}" for i in range(1, 61))
    value = "gh" + "p_" + GITHUB_TOKEN_TAIL
    (repo / "settings.py").write_text(f'{filler}\ntoken = "{value}"\n')

    ptr = _index_repo(repo)

    con = sqlite3.connect(Path(ptr["indexPath"]) / "mimry.sqlite")
    try:
        stored = con.execute("select body, content_hint from files_fts").fetchall()
    finally:
        con.close()
    for body, hint in stored:
        assert GITHUB_TOKEN_TAIL.lower() not in body.lower()
        assert GITHUB_TOKEN_TAIL.lower() not in hint.lower()


def test_body_terms_function_tokenizes_and_deduplicates(tmp_path):
    """body_terms correctly tokenizes and limits repetition.

    Verify that:
    - Raw identifiers are counted once
    - camelCase splits only for mixed-case tokens
    - Stopwords are filtered
    - Repetition is capped at 3
    - Results are sorted
    """
    # camelCase: raw + camel pieces (has uppercase)
    data = b"getUserToken getUserToken getUserToken getToken"
    result = body_terms(data)
    terms = result.split()

    # From 3 x "getUserToken" + 1 x "getToken": raw 3x+1x, camel pieces.
    getusertoken_count = terms.count("getusertoken")
    token_count = terms.count("token")
    get_count = terms.count("get")
    user_count = terms.count("user")

    assert getusertoken_count == 3, (
        f"'getusertoken' raw token should appear 3 times, got {getusertoken_count}"
    )
    assert token_count == 3, f"'token' pieces capped at 3, got {token_count}"
    assert user_count == 3, f"'user' pieces should appear 3 times, got {user_count}"
    assert get_count == 3, f"'get' pieces capped at 3, got {get_count}"
    assert terms == sorted(terms), f"Terms should be sorted: {terms}"

    # No camelCase split for all-lowercase: "partition" alone
    assert body_terms(b"partition") == "partition"
