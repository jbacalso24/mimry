from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from test_mimry_cli import copy_fixture, run_cli

from mimry.feedback import normalize_paths


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
