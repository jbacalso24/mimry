"""A YAML file nested past what the tree-sitter scanner can hold.

The YAML scanner keeps its indent stack in a fixed buffer. Past about
253 levels tree-sitter aborts the whole process, so mimry refuses such a
file before parsing it. The file gets no symbols, stays text-searchable
and is reported as unparsed, and the rest of the index is unaffected.
Every past-limit parse runs in a child process, so a regression
fails the test instead of killing pytest.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from mimry.core.languages import TOO_DEEPLY_NESTED, extract
from mimry.indexer import is_code_parse_failure

ROOT = Path(__file__).resolve().parents[1]

CHILD = """
import sys
from pathlib import Path
from mimry.core.languages import extract
result = extract(Path("deep.yaml"), sys.stdin.read())
print(result["status"], len(result["definitions"]))
"""


def _yaml(levels: int, width: int) -> str:
    """A mapping nested ``levels`` deep, ``width`` spaces per level."""
    return "a:\n" + "".join(" " * (width * i) + f"k{i}:\n" for i in range(1, levels + 1))


def _run_child(source: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-c", CHILD],
        input=source,
        text=True,
        encoding="utf-8",
        capture_output=True,
        env=env,
        timeout=120,
        check=False,
    )


def _run_cli(work: Path, cache: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["MIMRY_CACHE_HOME"] = str(cache)
    return subprocess.run(
        [sys.executable, "-m", "mimry.cli", "--root", str(work), *args],
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=300,
        check=False,
    )


def test_yaml_crash_depth_is_refused_in_a_child_process():
    # 300 levels is the depth from the issue, which aborted the process.
    result = _run_child(_yaml(300, width=2))
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == [TOO_DEEPLY_NESTED, "0"]


def test_yaml_one_level_past_the_limit_is_refused_in_a_child_process():
    # One space per level: 201 levels is 201 columns, past the limit.
    result = _run_child(_yaml(201, width=1))
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == [TOO_DEEPLY_NESTED, "0"]


def test_yaml_at_the_limit_keeps_its_symbols():
    # 200 levels is the limit itself, well inside the depth the
    # scanner holds.
    result = extract(Path("deep.yaml"), _yaml(200, width=1))
    assert result["status"] == "ok"
    names = {definition["name"] for definition in result["definitions"]}
    assert {"a", "a.k1"} <= names


def test_deep_yaml_is_reported_unparsed_and_the_index_still_works(tmp_path: Path):
    work = tmp_path / "repo"
    work.mkdir()
    (work / "deep.yaml").write_text(_yaml(300, width=2), encoding="utf-8")
    (work / "app.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    cache = tmp_path / "cache"

    init = _run_cli(work, cache, "init", "--skip-graph")
    assert init.returncode == 0, init.stderr

    index = _run_cli(work, cache, "index")
    assert index.returncode == 0, index.stderr
    assert "could not be parsed: deep.yaml" in index.stdout

    find_symbol = _run_cli(work, cache, "find", "hello")
    assert find_symbol.returncode == 0, find_symbol.stderr
    assert "app.py" in find_symbol.stdout

    find_text = _run_cli(work, cache, "find", "k1")
    assert find_text.returncode == 0, find_text.stderr
    assert "deep.yaml" in find_text.stdout

    preflight = _run_cli(work, cache, "preflight", "deep yaml")
    assert preflight.returncode == 0, preflight.stderr
    assert "deep.yaml" in preflight.stdout


def test_refused_status_counts_as_a_parse_failure():
    assert is_code_parse_failure(TOO_DEEPLY_NESTED, "tree-sitter-yaml")


def test_wide_indentation_counts_levels_not_columns():
    # 60 levels at 4 spaces reach column 240 but stay far from the
    # scanner's limit, so the file keeps its symbols.
    result = extract(Path("wide.yaml"), _yaml(60, width=4))
    assert result["status"] == "ok"
    assert "a.k1" in {definition["name"] for definition in result["definitions"]}
