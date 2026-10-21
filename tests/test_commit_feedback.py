from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_mimry_cli import copy_fixture, run_cli

from mimry.autoupdate import git_hooks_install
from mimry.feedback import (
    _redact_feedback_string,
    feedback_payload_from_args,
    list_feedback,
    parse_context_suggested_paths,
)
from mimry.installer import _posix_executable
from mimry.paths import TASK_PACK_PATTERN
from mimry.storage import load_pointer

OLD = "2020-01-01T00:00:00+0000"
requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


def _git(repo: Path, *args: str, date: str | None = None) -> str:
    env = os.environ.copy()
    if date:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    res = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return res.stdout.strip()


def _repo(tmp_path: Path, cache: Path) -> Path:
    """A git repo with MIMRY set up, indexed, and one commit at OLD."""
    repo = copy_fixture(tmp_path)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "MIMRY Test")
    _git(repo, "config", "user.email", "test@example.com")
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init", date=OLD)
    return repo


def _packs(repo: Path) -> list[Path]:
    context = repo / ".mimry" / "mimry-out" / "context"
    return sorted(p for p in context.iterdir() if TASK_PACK_PATTERN.match(p.name))


def _only_pack(repo: Path) -> Path:
    [pack] = _packs(repo)
    return pack


def _rows(repo: Path) -> list[dict]:
    ptr = load_pointer(repo)
    return list_feedback(Path(ptr["indexPath"]), ptr["rootId"], limit=1000)


def _utc(year: int, month: int, day: int) -> str:
    return datetime(year, month, day, tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%S+0000")


def _timestamp(year: int, month: int, day: int) -> float:
    return datetime(year, month, day, tzinfo=UTC).timestamp()


@requires_git
def test_commit_after_preflight_records_one_feedback_row(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    repo = _repo(tmp_path, cache)
    assert run_cli(repo, cache, "preflight", "fix auth session bug").returncode == 0
    pack = _only_pack(repo)
    suggested = parse_context_suggested_paths(pack)
    assert suggested
    picked = suggested[0]

    (repo / picked).write_text((repo / picked).read_text() + "\n# touched\n")
    (repo / "unrelated_new.txt").write_text("not suggested\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat")
    sha = _git(repo, "rev-parse", "HEAD")

    res = run_cli(repo, cache, "feedback", "--from-commit")
    assert res.returncode == 0, res.stderr
    assert "Saved feedback for" in res.stdout
    assert "2 changed" in res.stdout and "1 missed" in res.stdout

    [row] = _rows(repo)
    assert row["query"] == "fix auth session bug"
    assert row["suggested_paths"] == suggested
    assert sorted(row["changed_paths"]) == sorted([picked, "unrelated_new.txt"])
    assert row["missed_paths"] == ["unrelated_new.txt"]
    assert row["outcome"] == "unknown"
    assert row["notes"] == f"Recorded from commit {sha[:12]}"
    assert row["context_path"] == pack.resolve().relative_to(repo.resolve()).as_posix()

    stats = run_cli(repo, cache, "feedback", "stats")
    assert stats.returncode == 0, stats.stderr
    assert "1 from commits" in stats.stdout


@requires_git
def test_running_commit_feedback_twice_records_nothing_new(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    repo = _repo(tmp_path, cache)
    assert run_cli(repo, cache, "preflight", "fix auth session bug").returncode == 0
    picked = parse_context_suggested_paths(_only_pack(repo))[0]
    (repo / picked).write_text((repo / picked).read_text() + "\n# touched\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat")

    assert run_cli(repo, cache, "feedback", "--from-commit").returncode == 0
    again = run_cli(repo, cache, "feedback", "--from-commit")

    assert again.returncode == 0, again.stderr
    assert "feedback already recorded for this task" in again.stdout
    assert len(_rows(repo)) == 1


@requires_git
def test_commit_with_no_pack_since_previous_commit_records_nothing(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    repo = _repo(tmp_path, cache)
    assert run_cli(repo, cache, "preflight", "fix auth session bug").returncode == 0
    pack = _only_pack(repo)
    stamp = _timestamp(2020, 2, 1)
    os.utime(pack, (stamp, stamp))

    (repo / "first.txt").write_text("first\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "first", date=_utc(2020, 3, 1))

    (repo / "second.txt").write_text("second\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "second", date=_utc(2020, 4, 1))

    res = run_cli(repo, cache, "feedback", "--from-commit")

    assert res.returncode == 0, res.stderr
    assert "no context pack since the previous commit" in res.stdout
    assert _rows(repo) == []


@requires_git
def test_overlapping_pack_wins_over_a_newer_unrelated_pack(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    repo = _repo(tmp_path, cache)
    assert run_cli(repo, cache, "preflight", "fix auth session bug").returncode == 0
    overlapping = _only_pack(repo)
    picked = parse_context_suggested_paths(overlapping)[0]
    os.utime(overlapping, (_timestamp(2020, 2, 1), _timestamp(2020, 2, 1)))

    context = repo / ".mimry" / "mimry-out" / "context"
    unrelated = context / "rename-the-billing-invoice-00000001.md"
    unrelated.write_text(
        "# MIMRY Context Pack\n\n## Query\nrename the billing invoice\n\n"
        "## Relevant Files\n\n### 1. `docs/unrelated.md`\n"
    )
    os.utime(unrelated, (_timestamp(2020, 2, 15), _timestamp(2020, 2, 15)))

    (repo / picked).write_text((repo / picked).read_text() + "\n# touched\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat", date=_utc(2020, 3, 1))

    res = run_cli(repo, cache, "feedback", "--from-commit")

    assert res.returncode == 0, res.stderr
    [row] = _rows(repo)
    assert row["query"] == "fix auth session bug"
    assert row["context_path"] == overlapping.resolve().relative_to(repo.resolve()).as_posix()


@requires_git
def test_merge_commit_and_broad_commit_are_skipped(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    repo = _repo(tmp_path, cache)
    assert run_cli(repo, cache, "preflight", "fix auth session bug").returncode == 0
    picked = parse_context_suggested_paths(_only_pack(repo))[0]

    _git(repo, "checkout", "-q", "-b", "side")
    (repo / "side.txt").write_text("side\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "side")
    _git(repo, "checkout", "-q", "-")
    (repo / "main.txt").write_text("main\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge side", "side")

    merged = run_cli(repo, cache, "feedback", "--from-commit")
    assert merged.returncode == 0, merged.stderr
    assert "merge commit" in merged.stdout

    (repo / picked).write_text((repo / picked).read_text() + "\n# touched\n")
    bulk = repo / "bulk"
    bulk.mkdir()
    for i in range(50):
        (bulk / f"f{i}.txt").write_text(f"{i}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "broad")

    broad = run_cli(repo, cache, "feedback", "--from-commit")
    assert broad.returncode == 0, broad.stderr
    assert "commit changes 51 files, too broad to learn from" in broad.stdout
    assert _rows(repo) == []


@requires_git
def test_commit_feedback_in_unconfigured_repo_exits_zero(tmp_path):
    cache = tmp_path / "cache"
    repo = copy_fixture(tmp_path)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "MIMRY Test")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    res = run_cli(repo, cache, "feedback", "--from-commit", "HEAD")

    assert res.returncode == 0, res.stderr
    assert "MIMRY is not set up here" in res.stdout


@requires_git
def test_post_commit_hook_records_feedback_before_refresh(tmp_path, monkeypatch):
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")

    exe = _posix_executable()
    log = (repo.resolve() / ".git" / "mimry-refresh.log").as_posix()
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    old_block = (
        "#!/bin/sh\n# >>> mimry auto-update >>>\n"
        f"{exe} refresh >> {shlex.quote(log)} 2>&1 &\n# <<< mimry auto-update <<<\n"
    )
    (hooks_dir / "post-commit").write_text(old_block)

    assert git_hooks_install(repo) == 0
    content = (hooks_dir / "post-commit").read_text()

    expected = (
        f"{{ {exe} feedback --from-commit HEAD; {exe} refresh; }} >> {shlex.quote(log)} 2>&1 &"
    )
    assert expected in content.splitlines()
    assert f"{exe} refresh >> " not in content
    assert content.count("# >>> mimry auto-update >>>") == 1


@requires_git
def test_commit_touching_only_unsuggested_files_records_every_file_as_missed(
    tmp_path, monkeypatch
):
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    repo = _repo(tmp_path, cache)
    assert run_cli(repo, cache, "preflight", "fix auth session bug").returncode == 0
    suggested = parse_context_suggested_paths(_only_pack(repo))
    assert suggested

    (repo / "unrelated_a.txt").write_text("a\n")
    (repo / "unrelated_b.txt").write_text("b\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fix auth session bug")

    res = run_cli(repo, cache, "feedback", "--from-commit")

    assert res.returncode == 0, res.stderr
    assert "Saved feedback for" in res.stdout
    [row] = _rows(repo)
    assert row["suggested_paths"] == suggested
    assert sorted(row["changed_paths"]) == ["unrelated_a.txt", "unrelated_b.txt"]
    assert sorted(row["missed_paths"]) == ["unrelated_a.txt", "unrelated_b.txt"]


SHARE_QUERY = "remove share_extension.py leftovers"
# Same task, worded so the pack gets suggestions.
SHARE_QUERY_WITH_SUGGESTIONS = "remove share_extension.py leftovers from the auth session"


def _share_pack_repo(tmp_path, monkeypatch, query=SHARE_QUERY):
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    repo = _repo(tmp_path, cache)
    assert run_cli(repo, cache, "preflight", query).returncode == 0
    return repo, cache


@requires_git
def test_unrelated_commit_records_nothing_for_the_pack(tmp_path, monkeypatch):
    repo, cache = _share_pack_repo(tmp_path, monkeypatch)
    assert not {"README.md", ".gitignore"} & set(parse_context_suggested_paths(_only_pack(repo)))

    (repo / "README.md").write_text("# Typo fixed\n")
    (repo / ".gitignore").write_text("*.pyc\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Fix typo in README")

    res = run_cli(repo, cache, "feedback", "--from-commit")

    assert res.returncode == 0, res.stderr
    assert "commit shares no file or task word with the last context pack" in res.stdout
    assert _rows(repo) == []


@requires_git
def test_related_commit_records_unsuggested_readme_as_missed(tmp_path, monkeypatch):
    repo, cache = _share_pack_repo(tmp_path, monkeypatch)
    assert "README.md" not in parse_context_suggested_paths(_only_pack(repo))

    (repo / "README.md").write_text("# Share extension removed\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Remove share extension leftovers")

    res = run_cli(repo, cache, "feedback", "--from-commit")

    assert res.returncode == 0, res.stderr
    [row] = _rows(repo)
    assert row["query"] == SHARE_QUERY
    assert row["changed_paths"] == ["README.md"]
    assert row["missed_paths"] == ["README.md"]


@requires_git
def test_commit_with_a_file_named_after_the_task_is_related(tmp_path, monkeypatch):
    repo, cache = _share_pack_repo(tmp_path, monkeypatch)
    assert "share_extension.py" not in parse_context_suggested_paths(_only_pack(repo))

    (repo / "share_extension.py").write_text("# gone\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Tidy")

    res = run_cli(repo, cache, "feedback", "--from-commit")

    assert res.returncode == 0, res.stderr
    [row] = _rows(repo)
    assert row["missed_paths"] == ["share_extension.py"]


@requires_git
def test_commit_touching_a_suggested_file_records_whatever_its_message(tmp_path, monkeypatch):
    repo, cache = _share_pack_repo(tmp_path, monkeypatch, SHARE_QUERY_WITH_SUGGESTIONS)
    picked = parse_context_suggested_paths(_only_pack(repo))[0]

    (repo / picked).write_text((repo / picked).read_text() + "\n# touched\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Fix typo")

    res = run_cli(repo, cache, "feedback", "--from-commit")

    assert res.returncode == 0, res.stderr
    assert "Saved feedback for" in res.stdout
    [row] = _rows(repo)
    assert picked in row["changed_paths"]
    assert row["missed_paths"] == []


@requires_git
def test_agent_feedback_on_latest_pack_blocks_a_second_row_for_the_task(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    repo = _repo(tmp_path, cache)
    assert run_cli(repo, cache, "preflight", "fix auth session bug").returncode == 0
    picked = parse_context_suggested_paths(_only_pack(repo))[0]

    agent = run_cli(
        repo,
        cache,
        "feedback",
        "--query",
        "fix auth session bug",
        "--context",
        ".mimry/mimry-out/context/latest.md",
        "--changed",
        picked,
        "--outcome",
        "passed",
    )
    assert agent.returncode == 0, agent.stderr

    (repo / picked).write_text((repo / picked).read_text() + "\n# touched\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat")

    res = run_cli(repo, cache, "feedback", "--from-commit")

    assert res.returncode == 0, res.stderr
    assert "feedback already recorded for this task" in res.stdout
    [row] = _rows(repo)
    assert row["outcome"] == "passed"


@pytest.mark.parametrize(
    "text",
    [
        "pass the duplicate flag",
        "fix password reset email",
        "password reset.",
        "bypass=cache",
        "compass: north",
    ],
)
def test_feedback_keeps_prose_that_mentions_passwords(text):
    assert _redact_feedback_string(text) == (text, False)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("pass=hunter2xyz", "pass=[REDACTED]"),
        ('"pass": "hunter2xyz"', '"pass": [REDACTED]'),
        ('"password": "hunter2xyz"', '"password": [REDACTED]'),
        ("password hunter2", "password [REDACTED]"),
        ("passwd s3cret!", "passwd [REDACTED]"),
    ],
)
def test_feedback_redacts_password_values(text, expected):
    assert _redact_feedback_string(text) == (expected, True)


def test_feedback_redaction_is_linear_on_worst_case_input():
    for text in ("a." * 50_000, "secret." * 20_000):
        started = time.perf_counter()
        _redact_feedback_string(text)
        assert time.perf_counter() - started < 1.0

    started = time.perf_counter()
    redacted, changed = _redact_feedback_string("a." * 50_000 + "password=tomato-canary")
    assert time.perf_counter() - started < 1.0
    assert "tomato-canary" not in redacted
    assert changed


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("api_key=tomato-canary", "api_key=[REDACTED]"),
        ("config.password: tomato-canary", "config.password: [REDACTED]"),
        ("pass=tomato-canary", "pass=[REDACTED]"),
        ("by.pass=tomato-canary", "by.pass=[REDACTED]"),
        ("bypass=tomato-canary", "bypass=tomato-canary"),
        ('token = "tomato-canary"', "token = [REDACTED]"),
        ('"secret": "tomato-canary"', '"secret": [REDACTED]'),
        ("password=[REDACTED]", "password=[REDACTED]"),
        ("count=3", "count=3"),
        ("x-api-key: tomato-canary", "x-api-key: [REDACTED]"),
        ("url=https://x?password=tomato-canary", "url=https://x?password=[REDACTED]"),
        ("foo=secret=tomato-canary", "foo=secret=[REDACTED]"),
    ],
)
def test_feedback_secret_assignment_matches_previous_output(text, expected):
    assert _redact_feedback_string(text) == (expected, expected != text)


def test_feedback_payload_from_args_is_unchanged(tmp_path):
    args = SimpleNamespace(
        from_json=None,
        query="  fix auth session bug password=hunter2 ",
        context=None,
        suggested="src/mimry/feedback.py, ./src/mimry/commands.py,src/mimry/feedback.py",
        opened="docs/commands.md",
        changed=["src/mimry/autoupdate.py", "../outside.txt"],
        missed=None,
        ignored="tests/test_autoupdate.py",
        verification="uv run pytest -q passed",
        outcome="PASSED",
        notes="Bearer abc123 and more",
    )

    payload = feedback_payload_from_args(tmp_path, args)

    assert payload == {
        "changed_paths": ["src/mimry/autoupdate.py", "external:../outside.txt"],
        "context_path": None,
        "ignored_paths": ["tests/test_autoupdate.py"],
        "missed_paths": ["src/mimry/autoupdate.py"],
        "notes": "Bearer [REDACTED] and more",
        "opened_paths": ["docs/commands.md"],
        "outcome": "passed",
        "query": "fix auth session bug password=[REDACTED]",
        "redacted_fields": ["query", "notes"],
        "suggested_paths": ["src/mimry/feedback.py", "src/mimry/commands.py"],
        "verification": [{"command": "uv run pytest -q passed", "status": "unknown"}],
    }
