from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

from mimry import history, mcp_protocol, mcp_tools

ROOT = Path(__file__).resolve().parents[1]


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
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
        text=True,
        encoding="utf-8",
    )
    return result.stdout


def _mimry(cwd: Path, cache: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["MIMRY_CACHE_HOME"] = str(cache)
    return subprocess.run(
        [sys.executable, "-m", "mimry.cli", *args],
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    return path


def _commit(repo: Path, message: str, body: str = "") -> None:
    args = ["commit", "-q", "--allow-empty", "-m", message]
    if body:
        args += ["-m", body]
    _git(repo, *args)


def _write(repo: Path, rel: str, text: str = "content\n") -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _share_extension_repo(tmp_path: Path) -> tuple[Path, Path]:
    """A repo where one commit deletes the ShareExtension folder."""
    cache = tmp_path / "cache"
    repo = _init_repo(tmp_path / "repo")
    _write(repo, "ShareExtension/Info.plist", "info\n")
    _write(repo, "ShareExtension/ShareViewController.swift", "view\n")
    _write(repo, "App/Main.swift", "main\n")
    _git(repo, "add", "-A")
    _commit(repo, "Add the share extension")
    _write(repo, "App/Main.swift", "main v2\n")
    _git(repo, "add", "-A")
    _commit(repo, "Tidy the main file")
    _git(repo, "rm", "-rq", "ShareExtension")
    _commit(repo, "Remove the Share Extension target")
    assert _mimry(repo, cache, "init", "--skip-graph").returncode == 0
    return repo, cache


def _db(tmp_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(tmp_path / "history.sqlite")
    history.ensure_history_schema(con)
    return con


def _subjects(con: sqlite3.Connection) -> list[str]:
    return [row[0] for row in con.execute("select subject from commits order by id")]


def test_deleted_folder_commit_ranks_first_with_removed_status(tmp_path):
    repo, cache = _share_extension_repo(tmp_path)

    res = _mimry(repo, cache, "history", "share extension removed")

    assert res.returncode == 0, res.stderr
    first_commit = re.search(r"^  [0-9a-f]{7}  \d{4}-\d{2}-\d{2}  (.*)$", res.stdout, re.M)
    assert first_commit is not None, res.stdout
    assert first_commit.group(1) == "Remove the Share Extension target"
    assert "removed  ShareExtension/Info.plist" in res.stdout
    assert "removed  ShareExtension/ShareViewController.swift" in res.stdout


def test_rename_shows_the_new_path_and_the_old_one(tmp_path):
    cache = tmp_path / "cache"
    repo = _init_repo(tmp_path / "repo")
    _write(repo, "src/old_name.py", "print('one')\n")
    _git(repo, "add", "-A")
    _commit(repo, "Add the module")
    _git(repo, "mv", "src/old_name.py", "src/new_name.py")
    _commit(repo, "Rename the module")
    assert _mimry(repo, cache, "init", "--skip-graph").returncode == 0

    res = _mimry(repo, cache, "history", "module renamed")

    assert res.returncode == 0, res.stderr
    assert "renamed  src/new_name.py (from src/old_name.py)" in res.stdout


def _file_name_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Commits on app.py, share_extension.py, a rename and app notes."""
    cache = tmp_path / "cache"
    repo = _init_repo(tmp_path / "repo")
    _write(repo, "src/app.py", "app = 1\n")
    _git(repo, "add", "-A")
    _commit(repo, "Add the app")
    _write(repo, "src/share_extension.py", "share = 1\n")
    _git(repo, "add", "-A")
    _commit(repo, "Add the share extension")
    _git(repo, "mv", "src/app.py", "src/core.py")
    _commit(repo, "Rename the app")
    _write(repo, "docs/notes.md", "app py notes\n")
    _git(repo, "add", "-A")
    _commit(repo, "App py notes")
    assert _mimry(repo, cache, "init", "--skip-graph").returncode == 0
    return repo, cache


def test_file_name_query_lists_only_commits_touching_that_file(tmp_path):
    repo, cache = _file_name_repo(tmp_path)

    res = _mimry(repo, cache, "history", "app.py")

    assert res.returncode == 0, res.stderr
    subjects = re.findall(r"^  [0-9a-f]{7}  \d{4}-\d{2}-\d{2}  (.*)$", res.stdout, re.M)
    assert sorted(subjects) == ["Add the app", "Rename the app"], res.stdout


def test_word_query_still_matches_by_words(tmp_path):
    repo, cache = _file_name_repo(tmp_path)

    res = _mimry(repo, cache, "history", "share extension")

    assert res.returncode == 0, res.stderr
    assert "Add the share extension" in res.stdout


def test_rename_line_names_the_old_path_after_the_new_one(tmp_path):
    repo, cache = _file_name_repo(tmp_path)

    res = _mimry(repo, cache, "history", "app.py")

    assert res.returncode == 0, res.stderr
    assert "renamed  src/core.py (from src/app.py)" in res.stdout


def test_inexact_rename_is_listed_as_removed_and_added(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    _write(repo, "lib/parser.py", "".join(f"line {n}\n" for n in range(40)))
    _git(repo, "add", "-A")
    _commit(repo, "Add the parser")
    _git(repo, "mv", "lib/parser.py", "lib/reader.py")
    _write(repo, "lib/reader.py", "".join(f"line {n}\n" for n in range(39)) + "changed\n")
    _git(repo, "add", "-A")
    _commit(repo, "Move the parser")
    con = _db(tmp_path)

    history.update_history(con, repo)

    statuses = con.execute(
        "select cf.status, cf.path from commit_files cf join commits c on c.id = cf.commit_id "
        "where c.subject = 'Move the parser' order by cf.path"
    ).fetchall()
    assert statuses == [("D", "lib/parser.py"), ("A", "lib/reader.py")]


def test_new_commit_is_added_without_rebuilding(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    _write(repo, "a.txt")
    _git(repo, "add", "-A")
    _commit(repo, "Add a")
    con = _db(tmp_path)
    assert history.update_history(con, repo)["status"] == "rebuilt"
    con.execute("update commits set body = 'marker'")
    con.commit()
    _write(repo, "b.txt")
    _git(repo, "add", "-A")
    _commit(repo, "Add b")

    result = history.update_history(con, repo)

    assert result["status"] == "updated"
    assert result["added"] == 1
    assert _subjects(con) == ["Add a", "Add b"]
    # The first run's row keeps its marker, so nothing was rebuilt.
    assert con.execute("select count(*) from commits where body = 'marker'").fetchone()[0] == 1


def test_rewritten_head_rebuilds_the_history(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    _write(repo, "a.txt")
    _git(repo, "add", "-A")
    _commit(repo, "Add a")
    _write(repo, "b.txt")
    _git(repo, "add", "-A")
    _commit(repo, "Add b")
    con = _db(tmp_path)
    history.update_history(con, repo)
    _git(repo, "commit", "-q", "--amend", "-m", "Add b, reworded")

    result = history.update_history(con, repo)

    assert result["status"] == "rebuilt"
    assert _subjects(con) == ["Add a", "Add b, reworded"]


def test_committed_index_changes_reach_history_without_a_file_change(tmp_path):
    cache = tmp_path / "cache"
    repo = _init_repo(tmp_path / "repo")
    _write(repo, "app.py", "x = 1\n")
    _git(repo, "add", "-A")
    _commit(repo, "Add app")
    assert _mimry(repo, cache, "init", "--skip-graph").returncode == 0
    assert _mimry(repo, cache, "index").returncode == 0
    _write(repo, "app.py", "x = 2\n")
    assert _mimry(repo, cache, "index").returncode == 0
    _git(repo, "add", "-A")
    _commit(repo, "Bump app to two")

    assert _mimry(repo, cache, "index").returncode == 0
    res = _mimry(repo, cache, "history", "bump app")

    assert res.returncode == 0, res.stderr
    assert "Bump app to two" in res.stdout


def test_secret_in_commit_body_is_redacted_in_store_and_output(tmp_path):
    cache = tmp_path / "cache"
    repo = _init_repo(tmp_path / "repo")
    token = "ghp_" + "a" * 36
    _write(repo, "deploy.txt")
    _git(repo, "add", "-A")
    _commit(repo, "Wire up deploys", f"Used token {token} for the first run.")
    con = _db(tmp_path)
    history.update_history(con, repo)
    stored = con.execute("select subject, body from commits").fetchone()
    assert token not in " ".join(stored)
    assert len(stored[1]) <= history.HISTORY_BODY_CHARS

    assert _mimry(repo, cache, "init", "--skip-graph").returncode == 0
    res = _mimry(repo, cache, "history", "deploys")

    assert res.returncode == 0, res.stderr
    assert token not in res.stdout + res.stderr


def test_not_a_git_repo_exits_1_with_the_message(tmp_path):
    cache = tmp_path / "cache"
    folder = tmp_path / "plain"
    folder.mkdir()
    _write(folder, "notes.txt", "hello\n")
    assert _mimry(folder, cache, "init", "--skip-graph").returncode == 0

    res = _mimry(folder, cache, "history", "notes")

    assert res.returncode == 1
    assert "This folder is not a git repository" in res.stderr


def test_blank_query_exits_2(tmp_path):
    repo, cache = _share_extension_repo(tmp_path)

    res = _mimry(repo, cache, "history", "   ")

    assert res.returncode == 2


def test_no_match_exits_1(tmp_path):
    repo, cache = _share_extension_repo(tmp_path)

    res = _mimry(repo, cache, "history", "zzqxvb")

    assert res.returncode == 1
    assert 'No commits match "zzqxvb".' in res.stdout


def test_json_shape(tmp_path):
    repo, cache = _share_extension_repo(tmp_path)

    res = _mimry(repo, cache, "history", "share extension removed", "--json")

    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert set(payload) == {"query", "shallow", "commits"}
    assert payload["shallow"] is False
    commit = payload["commits"][0]
    assert set(commit) == {"sha", "shortSha", "date", "subject", "pathCount", "paths"}
    assert commit["subject"] == "Remove the Share Extension target"
    assert commit["shortSha"] == commit["sha"][:7]
    assert commit["pathCount"] == len(commit["paths"]) == 2
    assert set(commit["paths"][0]) == {"status", "path", "oldPath"}
    assert commit["paths"][0] == {
        "status": "removed",
        "path": "ShareExtension/Info.plist",
        "oldPath": None,
    }


def test_history_cap_keeps_only_the_newest_commits(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "HISTORY_COMMIT_LIMIT", 2)
    repo = _init_repo(tmp_path / "repo")
    for name in ("a", "b", "c", "d"):
        _write(repo, f"{name}.txt")
        _git(repo, "add", "-A")
        _commit(repo, f"Add {name}")
    con = _db(tmp_path)

    history.update_history(con, repo)
    assert _subjects(con) == ["Add c", "Add d"]

    _write(repo, "e.txt")
    _git(repo, "add", "-A")
    _commit(repo, "Add e")
    history.update_history(con, repo)
    assert _subjects(con) == ["Add d", "Add e"]
    assert con.execute("select count(*) from commit_files").fetchone()[0] == 2


def test_shallow_clone_is_noted_in_the_output(tmp_path):
    cache = tmp_path / "cache"
    source = _init_repo(tmp_path / "source")
    _write(source, "old.txt")
    _git(source, "add", "-A")
    _commit(source, "Add old file")
    _write(source, "new.txt")
    _git(source, "add", "-A")
    _commit(source, "Add new file")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", "--depth", "1", source.as_uri(), str(clone))
    assert _mimry(clone, cache, "init", "--skip-graph").returncode == 0
    indexed = _mimry(clone, cache, "index")
    assert indexed.returncode == 0, indexed.stderr
    assert "shallow history" in indexed.stdout

    res = _mimry(clone, cache, "history", "file", "--json")

    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload["shallow"] is True
    assert [commit["subject"] for commit in payload["commits"]] == ["Add new file"]
    text = _mimry(clone, cache, "history", "file")
    assert "shallow history" in text.stdout


def test_missing_git_marks_history_unavailable(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / "repo")
    _write(repo, "a.txt")
    _git(repo, "add", "-A")
    _commit(repo, "Add a")
    con = _db(tmp_path)
    history.update_history(con, repo)

    def no_git(root, *args):
        raise FileNotFoundError("git")

    monkeypatch.setattr(history, "_git", no_git)
    result = history.update_history(con, repo)

    assert result["status"] == "unavailable"
    assert result["reason"] == history.GIT_MISSING
    assert con.execute("select count(*) from commits").fetchone()[0] == 0


def test_query_with_no_word_characters_finds_nothing(tmp_path):
    con = _db(tmp_path)

    assert history.read_history(con, "!!", 10) == []


def test_version_number_in_the_query_finds_the_commit_that_names_it(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    for version in ("0.4.0", "0.4.1", "0.4.2"):
        _write(repo, f"notes/{version}.md")
        _git(repo, "add", "-A")
        _commit(repo, f"Release {version} (#1)")
    con = _db(tmp_path)
    history.update_history(con, repo)

    commits = history.read_history(con, "release 0.4.1", 10)

    assert commits[0]["subject"] == "Release 0.4.1 (#1)"
    assert len(commits) == 3


def test_a_new_history_format_rebuilds_the_commits(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / "repo")
    _write(repo, "a.txt")
    _git(repo, "add", "-A")
    _commit(repo, "Add a")
    con = _db(tmp_path)
    history.update_history(con, repo)
    con.execute("update commits set body = 'marker'")
    con.commit()
    assert history.update_history(con, repo)["status"] == "current"

    monkeypatch.setattr(history, "HISTORY_FORMAT", history.HISTORY_FORMAT + 1)

    assert history.update_history(con, repo)["status"] == "rebuilt"
    assert con.execute("select count(*) from commits where body = 'marker'").fetchone()[0] == 0


def test_mcp_history_returns_the_same_payload_as_the_cli_json(tmp_path, monkeypatch):
    repo, cache = _share_extension_repo(tmp_path)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))

    via_mcp = mcp_tools.mimry_history("share extension removed", root=str(repo))

    cli = json.loads(_mimry(repo, cache, "history", "share extension removed", "--json").stdout)
    # The MCP call refreshes the index first, adding a reindexed note.
    assert {key: value for key, value in via_mcp.items() if key != "reindexed"} == cli
    assert via_mcp["commits"][0]["subject"] == "Remove the Share Extension target"


def test_mcp_history_blank_query_is_an_error(tmp_path, monkeypatch):
    repo, cache = _share_extension_repo(tmp_path)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))

    payload = mcp_tools.mimry_history("   ", root=str(repo))

    assert payload["error"]["code"] == "empty_query"
    assert mcp_protocol._failed(payload)


def _context_pack(repo: Path) -> str:
    return (repo / ".mimry" / "mimry-out" / "context" / "latest.md").read_text(encoding="utf-8")


def test_preflight_names_the_commit_that_removed_a_named_path(tmp_path):
    repo, cache = _share_extension_repo(tmp_path)

    res = _mimry(repo, cache, "preflight", "restore ShareExtension/Info.plist")

    assert res.returncode == 0, res.stderr
    line = (
        r"History: ShareExtension/Info\.plist was removed in [0-9a-f]{7} \d{4}-\d{2}-\d{2}"
        r' "Remove the Share Extension target"'
    )
    assert re.search(line, res.stdout), res.stdout
    assert re.search(line, _context_pack(repo))


def test_preflight_adds_no_history_line_for_a_path_the_index_has(tmp_path):
    repo, cache = _share_extension_repo(tmp_path)

    res = _mimry(repo, cache, "preflight", "fix App/Main.swift")

    assert res.returncode == 0, res.stderr
    assert "History:" not in res.stdout
    assert "## Commit History" not in _context_pack(repo)


def test_preflight_adds_no_history_line_for_a_task_with_no_path(tmp_path):
    repo, cache = _share_extension_repo(tmp_path)

    res = _mimry(repo, cache, "preflight", "fix the login timeout")

    assert res.returncode == 0, res.stderr
    assert "History:" not in res.stdout
    assert "## Commit History" not in _context_pack(repo)


def test_comment_like_secret_lines_in_a_commit_body_leave_no_canary_in_the_cache(tmp_path):
    # #520 audit: a # or // line inside a triple-quoted string or a YAML
    # block was kept as a comment, so its secret reached the history.
    cache = tmp_path / "cache"
    repo = _init_repo(tmp_path / "repo")
    _write(repo, "deploy.txt")
    _git(repo, "add", "-A")
    body = 'SECRET = """\n#tomato-canary\n"""\n\npassword: |\n  // tomato-canary\nhost: h\n'
    _commit(repo, "Wire up deploys", body)
    assert _mimry(repo, cache, "init", "--skip-graph").returncode == 0
    res = _mimry(repo, cache, "history", "deploys")

    assert res.returncode == 0, res.stderr
    assert "tomato-canary" not in res.stdout + res.stderr
    for root in (cache, repo / ".mimry"):
        for path in root.rglob("*"):
            if path.is_file():
                assert b"tomato-canary" not in path.read_bytes(), path
