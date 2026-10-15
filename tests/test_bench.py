from __future__ import annotations

import json
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from mimry.bench import (
    NO_CANDIDATES,
    SKIP_NO_MODIFIED,
    SKIP_TOO_BROAD,
    _task_query,
    run_bench,
    score_task,
    select_tasks,
    summarize,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=MIMRY Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _commit(repo: Path, message: str, files: dict[str, str]) -> str:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _new_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(
        repo,
        "Initial import",
        {
            "src/parser.py": "def parse(text):\n    return text.split()\n",
            "src/lexer.py": "def lex(text):\n    return list(text)\n",
            "docs/guide.md": "# Guide\n",
        },
    )
    return repo


def test_select_picks_squash_and_merge_commits_with_base_and_gold(tmp_path):
    repo = _new_repo(tmp_path)
    init = _git(repo, "rev-parse", "HEAD")
    squash = _commit(
        repo,
        "Fix parser (#12)\n\nHandle empty input.\n\nMore detail nobody needs.",
        {"src/parser.py": "def parse(text):\n    return text.split() or []\n"},
    )
    _git(repo, "checkout", "-q", "-b", "feature/flags")
    _commit(
        repo,
        "Add flags",
        {"src/flags.py": "FLAGS = []\n", "src/lexer.py": "def lex(text):\n    return []\n"},
    )
    _git(repo, "checkout", "-q", "main")
    _git(
        repo,
        "merge",
        "--no-ff",
        "-q",
        "feature/flags",
        "-m",
        "Merge pull request #7 from user/feature/flags\n\nAdd flag parsing\n\nLonger body.",
    )
    merge = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "Tweak the guide", {"docs/guide.md": "# Guide v2\n"})
    release = _commit(repo, "Release notes (#15)", {"CHANGELOG.md": "# Changes\n\n- one\n"})
    bump = _commit(
        repo,
        "Bump deps (#16)",
        {"uv.lock": "version = 2\n", "src/parser.py": "def parse(text):\n    return []\n"},
    )
    _commit(
        repo,
        "Mass rename (#17)",
        {f"bulk/file{i:02}.txt": f"{i}\n" for i in range(31)},
    )

    selection = select_tasks(repo, 20)

    # The plain "Tweak the guide" commit is not a candidate at all.
    assert selection.candidates == 5
    assert selection.skipped == Counter({SKIP_TOO_BROAD: 1, SKIP_NO_MODIFIED: 1})
    assert [task.commit for task in selection.tasks] == [bump, merge, squash]

    bump_task, merge_task, squash_task = selection.tasks
    assert bump_task.query == "Bump deps"
    assert bump_task.base == release
    assert bump_task.gold == ("src/parser.py",)  # uv.lock is excluded

    assert merge_task.query == "Add flag parsing"
    assert merge_task.base == squash
    # src/flags.py is added by the branch, so it is not gold.
    assert merge_task.gold == ("src/lexer.py",)

    assert squash_task.query == "Fix parser Handle empty input."
    assert squash_task.base == init
    assert squash_task.gold == ("src/parser.py",)


def test_select_stops_at_the_requested_number_of_tasks(tmp_path):
    repo = _new_repo(tmp_path)
    _commit(repo, "First (#1)", {"src/parser.py": "a\n"})
    second = _commit(repo, "Second (#2)", {"src/lexer.py": "b\n"})
    third = _commit(repo, "Third (#3)", {"docs/guide.md": "c\n"})

    selection = select_tasks(repo, 2)

    assert [task.commit for task in selection.tasks] == [third, second]
    assert selection.candidates == 2


def test_no_qualifying_commits_says_so_and_exits_zero(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "Initial", {"a.py": "x\n"})
    _commit(repo, "Plain change", {"a.py": "y\n"})

    assert run_bench(repo, tasks=20) == 0
    out = capsys.readouterr().out
    assert NO_CANDIDATES in out
    assert "merge commits" in out
    assert "(#N)" in out


def test_score_ranks_recall_and_charges_tokens_up_to_last_gold(tmp_path):
    ranked = ["a.py", "b.py", "c.py", "d.py", "e.py", "f.py"]
    charges = {"a.py": 10, "b.py": 20, "c.py": 30, "d.py": 40, "e.py": 50, "f.py": 60}

    score = score_task(ranked, ["b.py", "f.py"], charges.__getitem__)

    assert score["gold_ranks"] == {"b.py": 2, "f.py": 6}
    assert score["first_gold_rank"] == 2
    assert score["recall_at_5"] == 0.5
    assert score["recall_at_10"] == 1.0
    assert score["tokens"] == sum(charges.values())


def test_tokens_stop_at_the_last_gold_file_in_the_top_ten():
    charges = {"a.py": 10, "b.py": 20, "c.py": 30, "d.py": 40}

    score = score_task(["a.py", "b.py", "c.py", "d.py"], ["b.py"], charges.__getitem__)

    assert score["tokens"] == 30  # a.py and b.py only
    assert score["recall_at_5"] == 1.0


def test_no_gold_in_the_top_ten_means_no_rank_and_no_tokens():
    ranked = [f"noise{i}.py" for i in range(12)] + ["gold.py"]

    score = score_task(ranked, ["gold.py"], lambda path: 1)

    assert score["gold_ranks"] == {"gold.py": None}
    assert score["first_gold_rank"] is None
    assert score["tokens"] is None
    assert score["recall_at_5"] == 0.0
    assert score["recall_at_10"] == 0.0


def test_summary_counts_located_tasks_and_skips():
    results = [
        {
            "first_gold_rank": 1,
            "recall_at_5": 1.0,
            "recall_at_10": 1.0,
            "tokens": 100,
            "find_ms": 10.0,
        },
        {
            "first_gold_rank": 3,
            "recall_at_5": 0.5,
            "recall_at_10": 1.0,
            "tokens": 300,
            "find_ms": 30.0,
        },
        {
            "first_gold_rank": None,
            "recall_at_5": 0.0,
            "recall_at_10": 0.0,
            "tokens": None,
            "find_ms": 20.0,
        },
    ]

    summary = summarize(results, Counter({SKIP_NO_MODIFIED: 2}))

    assert summary["tasks"] == 3
    assert summary["skipped"] == 2
    assert summary["located"] == 2
    assert summary["mean_recall_at_5"] == pytest.approx(0.5)
    assert summary["median_first_gold_rank"] == 2
    assert summary["share_gold_at_rank_1"] == pytest.approx(1 / 3)
    assert summary["median_tokens_located"] == 200
    assert summary["median_find_ms"] == 20.0
    assert summary["p95_find_ms"] == 30.0


def test_end_to_end_run_leaves_the_repo_and_its_worktrees_untouched(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(
        repo,
        "Initial import",
        {
            "src/parser.py": "def parse(text):\n    return text.split()\n",
            "src/lexer.py": "def lex(text):\n    return list(text)\n",
            "docs/guide.md": "# Guide\n",
        },
    )
    _commit(repo, "Fix parser (#12)", {"src/parser.py": "def parse(text):\n    return []\n"})
    _commit(repo, "Add lexer tokens (#13)", {"src/lexer.py": "def lex(text):\n    return []\n"})
    _commit(repo, "Document the guide (#14)", {"docs/guide.md": "# Guide\n\nUse MIMRY.\n"})
    before = _git(repo, "status", "--porcelain")

    assert run_bench(repo, tasks=3, as_json=True) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["tasks"] == 3
    assert [task["query"] for task in payload["tasks"]] == [
        "Fix parser",
        "Add lexer tokens",
        "Document the guide",
    ]
    assert _git(repo, "status", "--porcelain") == before == ""
    assert not (repo / ".mimry").exists()
    assert len(_git(repo, "worktree", "list").splitlines()) == 1


def test_added_files_are_not_gold(tmp_path):
    repo = _new_repo(tmp_path)
    _commit(
        repo,
        "Extend parser (#2)",
        {
            "src/parser.py": "def parse(text):\n    return []\n",
            "src/new_module.py": "NEW = True\n",
        },
    )

    [task] = select_tasks(repo, 20).tasks

    assert task.gold == ("src/parser.py",)


def test_a_task_with_only_added_files_is_skipped(tmp_path):
    repo = _new_repo(tmp_path)
    _commit(repo, "Add module (#2)", {"src/new_module.py": "NEW = True\n"})

    selection = select_tasks(repo, 20)

    assert selection.tasks == []
    assert selection.candidates == 1
    assert selection.skipped == Counter({SKIP_NO_MODIFIED: 1})


@pytest.mark.parametrize(
    ("subject", "body", "query"),
    [
        ("Release 0.3.4 (#248)", "* Release 0.3.4\n* Bump versions", "Release 0.3.4"),
        (
            "Fix x (#3)",
            "Co-authored-by: A <a@example.com>\nSigned-off-by: B <b@example.com>",
            "Fix x",
        ),
        ("Fix y (#4)", "- first\n- second\n\nMore text below", "Fix y"),
        ("Fix z (#5)", "Handle empty input.\n* extra", "Fix z Handle empty input. * extra"),
    ],
)
def test_a_squash_body_of_only_bullets_or_trailers_is_dropped(subject, body, query):
    assert _task_query(["parent"], subject, body) == query
