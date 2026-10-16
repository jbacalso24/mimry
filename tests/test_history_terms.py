from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from mimry import history, search
from mimry.storage import connect

ROOT = Path(__file__).resolve().parents[1]


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=MIMRY Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.autocrlf=false",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    return repo


def _commit(repo: Path, message: str, files: dict[str, str]) -> None:
    for rel, text in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)


def _index(tmp_path: Path, repo: Path, paths: list[str]) -> Path:
    """An index dir with commit tables and a files_fts row per path."""
    idx = tmp_path / "idx"
    con = connect(idx)
    history.update_history(con, repo)
    for rel in paths:
        name = Path(rel).name
        con.execute(
            "insert into files_fts(file_id, rel_path, filename, extension, content_hint,"
            " metadata_text, body, history_terms) values(?,?,?,?,?,?,?,?)",
            (rel, rel, name, Path(rel).suffix, "", "", "", None),
        )
    history.refresh_history_terms(con, search.HISTORY_TERMS_WEIGHT)
    con.commit()
    con.close()
    return idx


def _terms(idx: Path) -> dict[str, str]:
    con = connect(idx)
    try:
        return {
            rel: text or ""
            for rel, text in con.execute("select rel_path, history_terms from files_fts")
        }
    finally:
        con.close()


def test_subject_words_match_the_file_they_touched(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "Add the files", {"a.py": "a\n", "b.py": "b\n"})
    _commit(repo, "Fix cookie refresh (#123)", {"b.py": "b2\n"})
    idx = _index(tmp_path, repo, ["a.py", "b.py"])

    terms = _terms(idx)
    assert "cookie" in terms["b.py"].split()
    assert "refresh" in terms["b.py"].split()
    assert "cookie" not in terms["a.py"].split()

    scores = search._fts_scores(idx, "cookie refresh")
    assert set(scores) == {"b.py"}


def test_zero_weight_leaves_the_column_empty_and_scores_unchanged(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _commit(repo, "Add the files", {"a.py": "a\n", "b.py": "b\n"})
    _commit(repo, "Fix cookie refresh", {"b.py": "b2\n"})
    monkeypatch.setattr(search, "HISTORY_TERMS_WEIGHT", 0)
    idx = _index(tmp_path, repo, ["a.py", "b.py"])

    assert all(text == "" for text in _terms(idx).values())
    assert search._fts_scores(idx, "cookie refresh") == {}

    con = connect(idx)
    old = con.execute(
        "select file_id, bm25(files_fts, 0.0, 1.0, 1.4, 0.4, 0.8, 0.8, 0.8) from files_fts "
        "where files_fts match ? order by file_id",
        ('"b"*',),
    ).fetchall()
    new = con.execute(
        "select file_id, bm25(files_fts, 0.0, 1.0, 1.4, 0.4, 0.8, 0.8, 0.8, 0.0) from files_fts "
        "where files_fts match ? order by file_id",
        ('"b"*',),
    ).fetchall()
    con.close()
    assert old == new and old


def test_mass_commit_over_the_cap_adds_no_terms(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "HISTORY_TERMS_MAX_FILES", 2)
    repo = _repo(tmp_path)
    _commit(repo, "Add the files", {"a.py": "a\n", "b.py": "b\n", "c.py": "c\n"})
    _commit(repo, "Reformat everything", {"a.py": "a2\n", "b.py": "b2\n", "c.py": "c2\n"})
    _commit(repo, "Fix cookie refresh", {"b.py": "b3\n"})
    idx = _index(tmp_path, repo, ["a.py", "b.py", "c.py"])

    terms = _terms(idx)
    assert "reformat" not in terms["a.py"].split()
    assert "reformat" not in terms["b.py"].split()
    assert "cookie" in terms["b.py"].split()


def test_renamed_file_keeps_the_terms_from_before_the_rename(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "Fix cookie refresh", {"a.py": "a\n"})
    _git(repo, "mv", "a.py", "b.py")
    _git(repo, "commit", "-q", "-m", "Move the module")
    idx = _index(tmp_path, repo, ["b.py"])

    words = _terms(idx)["b.py"].split()
    assert "cookie" in words
    assert "refresh" in words
    assert "move" in words


def test_only_the_newest_commits_count(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "HISTORY_TERMS_COMMITS", 2)
    repo = _repo(tmp_path)
    _commit(repo, "Alpha work", {"b.py": "1\n"})
    _commit(repo, "Beta work", {"b.py": "2\n"})
    _commit(repo, "Gamma work", {"b.py": "3\n"})
    idx = _index(tmp_path, repo, ["b.py"])

    words = _terms(idx)["b.py"].split()
    assert "gamma" in words
    assert "beta" in words
    assert "alpha" not in words


def test_pr_numbers_and_redaction_placeholder_are_dropped(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "Fix cookie [REDACTED] refresh (#123)", {"b.py": "b\n"})
    idx = _index(tmp_path, repo, ["b.py"])

    words = _terms(idx)["b.py"].split()
    assert "cookie" in words
    assert "refresh" in words
    assert "redacted" not in words
    assert "123" not in words


def test_new_commit_reaches_the_index_without_a_rebuild(tmp_path):
    repo = _repo(tmp_path)
    cache = tmp_path / "cache"
    _commit(repo, "Add the files", {"a.py": "def a():\n    return 1\n", "b.py": "b\n"})
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["MIMRY_CACHE_HOME"] = str(cache)

    def mimry(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "mimry.cli", *args],
            cwd=repo,
            env=env,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

    assert mimry("init", "--skip-graph").returncode == 0
    assert mimry("index").returncode == 0
    _commit(repo, "Fix cookie refresh", {"b.py": "b2\n"})
    assert mimry("index").returncode == 0

    found = mimry("find", "cookie refresh", "--limit", "1")
    assert found.returncode == 0, found.stderr
    assert "b.py" in found.stdout
