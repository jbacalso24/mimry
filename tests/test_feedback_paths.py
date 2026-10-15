from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from test_mimry_cli import copy_fixture, run_cli

from mimry.feedback import (
    apply_feedback_to_rows,
    feedback_payload,
    normalize_paths,
    record_feedback,
)


def test_normalize_keeps_leading_dots_and_drops_dot_slash(tmp_path: Path):
    root = tmp_path.resolve()
    paths = normalize_paths(
        root,
        [".github/workflows/ci.yml", ".env.example", "./src/a.py", ".", "./"],
    )
    assert paths == [".github/workflows/ci.yml", ".env.example", "src/a.py"]


@pytest.mark.skipif(sys.platform != "win32", reason="a leading slash is relative only on Windows")
def test_normalize_drops_a_leading_slash_on_windows(tmp_path: Path):
    root = tmp_path.resolve()
    assert normalize_paths(root, ["/src/b.py"]) == ["src/b.py"]


def test_normalize_absolute_inside_root_is_relative_and_outside_is_external(tmp_path: Path):
    root = (tmp_path / "repo").resolve()
    root.mkdir()
    inside = root / "src" / "a.py"
    paths = normalize_paths(root, [str(inside), "../x.py"])
    assert paths[0] == "src/a.py"
    assert paths[1].startswith("external:")


def test_dotfile_feedback_boost_reaches_the_ranking(tmp_path: Path):
    repo = copy_fixture(tmp_path)
    workflow = repo / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: ci\n# fix ci workflow\n", encoding="utf-8")
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    recorded = run_cli(
        repo,
        cache,
        "feedback",
        "--query",
        "release pipeline rollout",
        "--changed",
        ".github/workflows/ci.yml",
        "--outcome",
        "passed",
    )
    assert recorded.returncode == 0, recorded.stderr

    res = run_cli(repo, cache, "find", "release pipeline rollout", "--verbose")
    assert res.returncode == 0, res.stderr
    lines = res.stdout.splitlines()
    start = next((i for i, line in enumerate(lines) if ".github/workflows/ci.yml" in line), None)
    assert start is not None, res.stdout
    block = [lines[start]]
    for line in lines[start + 1 :]:
        if re.match(r"\s*\d+\s", line):
            break
        block.append(line)
    assert "feedback changed-file boost" in "\n".join(block), res.stdout


FEEDBACK_QUERY = "fix release pipeline rollout"


def _feedback_index(tmp_path: Path, name: str, records: list[dict]) -> Path:
    idx = tmp_path / name / "idx"
    idx.mkdir(parents=True)
    for data in records:
        record_feedback(idx, "root", feedback_payload(tmp_path, data))
    return idx


def _rows(*pairs: tuple[str, int]) -> list[dict]:
    return [{"path": path, "score": score, "reason": "graph baseline"} for path, score in pairs]


def _score(rows: list[dict], path: str) -> int:
    return next(row["score"] for row in rows if row["path"] == path)


def test_changed_file_for_an_identical_query_climbs_above_a_30_percent_higher_file(tmp_path):
    rows = _rows(("src/rival.py", 130), ("src/target.py", 100))
    idx = _feedback_index(
        tmp_path,
        "identical",
        [{"query": FEEDBACK_QUERY, "changed": ["src/target.py"], "outcome": "passed"}],
    )

    ranked = apply_feedback_to_rows(rows, idx, "root", FEEDBACK_QUERY)

    assert [row["path"] for row in ranked] == ["src/target.py", "src/rival.py"]


def test_loosely_similar_query_gives_a_smaller_gain_than_an_identical_one(tmp_path):
    rows = _rows(("src/rival.py", 130), ("src/target.py", 100))
    idx = _feedback_index(
        tmp_path,
        "loose",
        [{"query": "release pipeline notes", "changed": ["src/target.py"], "outcome": "passed"}],
    )

    loose = _score(apply_feedback_to_rows(rows, idx, "root", FEEDBACK_QUERY), "src/target.py")
    identical = _score(
        apply_feedback_to_rows(rows, idx, "root", "release pipeline notes"), "src/target.py"
    )

    assert 100 < loose < identical


def test_commit_feedback_with_unknown_outcome_gains_as_much_as_a_passed_record(tmp_path):
    rows = _rows(("src/rival.py", 130), ("src/target.py", 100))
    commit = _feedback_index(
        tmp_path, "commit", [{"query": FEEDBACK_QUERY, "changed": ["src/target.py"]}]
    )
    passed = _feedback_index(
        tmp_path,
        "passed",
        [{"query": FEEDBACK_QUERY, "changed": ["src/target.py"], "outcome": "passed"}],
    )

    from_commit = _score(
        apply_feedback_to_rows(rows, commit, "root", FEEDBACK_QUERY), "src/target.py"
    )
    from_passed = _score(
        apply_feedback_to_rows(rows, passed, "root", FEEDBACK_QUERY), "src/target.py"
    )

    assert from_commit == from_passed > 100


def test_failed_review_still_credits_changed_files_but_opened_files_count_less(tmp_path):
    rows = _rows(("src/rival.py", 130), ("src/target.py", 100))
    passed = _feedback_index(
        tmp_path,
        "passed",
        [{"query": FEEDBACK_QUERY, "changed": ["src/target.py"], "outcome": "passed"}],
    )
    failed = _feedback_index(
        tmp_path,
        "failed",
        [{"query": FEEDBACK_QUERY, "changed": ["src/target.py"], "outcome": "failed"}],
    )
    opened_passed = _feedback_index(
        tmp_path,
        "opened_passed",
        [{"query": FEEDBACK_QUERY, "opened": ["src/target.py"], "outcome": "passed"}],
    )
    opened_failed = _feedback_index(
        tmp_path,
        "opened_failed",
        [{"query": FEEDBACK_QUERY, "opened": ["src/target.py"], "outcome": "failed"}],
    )

    def score(idx: Path) -> int:
        return _score(apply_feedback_to_rows(rows, idx, "root", FEEDBACK_QUERY), "src/target.py")

    assert score(failed) == score(passed) > 100
    assert score(opened_failed) < score(opened_passed) < score(passed)


def test_file_both_changed_and_opened_in_one_record_counts_once(tmp_path):
    rows = _rows(("src/rival.py", 130), ("src/target.py", 100))
    changed_only = _feedback_index(
        tmp_path, "changed", [{"query": FEEDBACK_QUERY, "changed": ["src/target.py"]}]
    )
    both = _feedback_index(
        tmp_path,
        "both",
        [{"query": FEEDBACK_QUERY, "changed": ["src/target.py"], "opened": ["src/target.py"]}],
    )

    assert _score(apply_feedback_to_rows(rows, both, "root", FEEDBACK_QUERY), "src/target.py") == (
        _score(apply_feedback_to_rows(rows, changed_only, "root", FEEDBACK_QUERY), "src/target.py")
    )


def test_suggestion_unused_in_three_similar_records_decays_but_not_in_two(tmp_path):
    rows = _rows(("src/stale.py", 100), ("src/other.py", 90))
    record = {
        "query": FEEDBACK_QUERY,
        "suggested": ["src/stale.py", "src/fixed.py"],
        "changed": ["src/fixed.py"],
        "outcome": "passed",
    }
    two = _feedback_index(tmp_path, "two", [record, record])
    three = _feedback_index(tmp_path, "three", [record, record, record])

    stale_after_two = _score(
        apply_feedback_to_rows(rows, two, "root", FEEDBACK_QUERY), "src/stale.py"
    )
    ranked = apply_feedback_to_rows(rows, three, "root", FEEDBACK_QUERY)
    stale_after_three = _score(ranked, "src/stale.py")

    assert stale_after_two == 100
    assert stale_after_three == 80
    assert "feedback unused suggestion decay" in next(
        row["reason"] for row in ranked if row["path"] == "src/stale.py"
    )


def test_ignored_suggestion_lowers_the_score_but_never_below_one(tmp_path):
    rows = _rows(("src/noisy.py", 10), ("src/big.py", 100))
    one = _feedback_index(
        tmp_path, "one", [{"query": FEEDBACK_QUERY, "ignored": ["src/noisy.py"]}]
    )
    many = _feedback_index(
        tmp_path,
        "many",
        [{"query": FEEDBACK_QUERY, "ignored": ["src/noisy.py"]} for _ in range(10)],
    )

    assert (
        1 <= _score(apply_feedback_to_rows(rows, one, "root", FEEDBACK_QUERY), "src/noisy.py") < 10
    )
    assert _score(apply_feedback_to_rows(rows, many, "root", FEEDBACK_QUERY), "src/noisy.py") == 1


def test_no_feedback_returns_the_rows_unchanged(tmp_path):
    rows = _rows(("src/a.py", 30), ("src/b.py", 20))

    assert apply_feedback_to_rows(rows, tmp_path / "missing", "root", FEEDBACK_QUERY) == rows
