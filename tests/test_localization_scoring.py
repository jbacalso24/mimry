"""Unit tests for the localization scorer and its parsers.

Covers the scorer, the patch parser and the find output parser. No
network: the dataset is never fetched here.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

LOC = Path(__file__).resolve().parents[1] / "benchmarks" / "localization"
# run.py imports build_tasks as a sibling module, so the folder must be
# on the path first.
sys.path.insert(0, str(LOC))
import build_tasks  # noqa: E402


def _load_run():
    spec = importlib.util.spec_from_file_location("localization_run", LOC / "run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run = _load_run()

MODIFY = "\n".join(
    [
        "diff --git a/pkg/mod.py b/pkg/mod.py",
        "index 111..222 100644",
        "--- a/pkg/mod.py",
        "+++ b/pkg/mod.py",
        "@@ -1,2 +1,2 @@",
        "-x = 1",
        "+x = 2",
        " y = 3",
        "",
    ]
)
DELETE = "\n".join(
    [
        "diff --git a/old/gone.py b/old/gone.py",
        "deleted file mode 100644",
        "index 333..0000",
        "--- a/old/gone.py",
        "+++ /dev/null",
        "@@ -1 +0,0 @@",
        "-bye",
        "",
    ]
)
ADD = "\n".join(
    [
        "diff --git a/new/file.py b/new/file.py",
        "new file mode 100644",
        "index 0000000..444",
        "--- /dev/null",
        "+++ b/new/file.py",
        "@@ -0,0 +1 @@",
        "+hi",
        "",
    ]
)
RENAME = "\n".join(
    [
        "diff --git a/src/old_name.py b/src/new_name.py",
        "similarity index 90%",
        "rename from src/old_name.py",
        "rename to src/new_name.py",
        "index 555..666 100644",
        "--- a/src/old_name.py",
        "+++ b/src/new_name.py",
        "@@ -1 +1 @@",
        "-a",
        "+b",
        "",
    ]
)


def test_all_gold_in_top_one():
    result = run.score(["a.py", "b.py"], ["a.py"])
    assert result["acc_at_1"] and result["acc_at_5"] and result["acc_at_10"]
    assert result["recall_at_5"] == 1.0
    assert result["first_gold_rank"] == 1
    assert result["missed"] == []


def test_gold_split_across_ranks_two_and_seven():
    ranked = ["x0", "a.py", "x2", "x3", "x4", "x5", "b.py", "x7", "x8", "x9"]
    result = run.score(ranked, ["a.py", "b.py"])
    assert not result["acc_at_1"]
    assert not result["acc_at_5"]
    assert result["acc_at_10"]
    assert result["recall_at_5"] == 0.5
    assert result["recall_at_10"] == 1.0
    assert result["first_gold_rank"] == 2
    assert result["missed"] == []


def test_one_gold_missing_and_outside_top_ten():
    ranked = ["a.py"] + [f"x{i}" for i in range(10)] + ["b.py"]
    result = run.score(ranked, ["a.py", "b.py"])
    assert not result["acc_at_10"]
    assert result["recall_at_10"] == 0.5
    assert result["missed"] == ["b.py"]
    assert result["first_gold_rank"] == 1


def test_duplicate_paths_keep_their_first_rank():
    result = run.score(["a.py", "x", "a.py", "b.py"], ["b.py"])
    assert result["first_gold_rank"] == 3
    assert result["acc_at_5"]


def test_empty_gold_is_rejected():
    with pytest.raises(ValueError):
        run.score(["a.py"], [])


def test_parse_find_reads_ranked_paths_only():
    output = "\n".join(
        [
            "Indexed first - 12 files (1.2s)",
            "",
            'Best matches for "fix the bug"',
            "",
            "   1  pkg/a.py",
            "      matches the query terms",
            "      read first: lines 1-5 (f)",
            "   2  pkg/b.py",
            "      matches the query terms",
            "",
        ]
    )
    assert run.parse_find(output) == ["pkg/a.py", "pkg/b.py"]
    assert run.parse_find('No files match "x".\n') == []
    with pytest.raises(RuntimeError):
        run.parse_find("Something unexpected\n")


def test_parse_patch_modify_delete_add_rename():
    gold, added = build_tasks.parse_patch(MODIFY + DELETE + ADD + RENAME)
    assert gold == ["old/gone.py", "pkg/mod.py", "src/old_name.py"]
    assert added == 1


def test_parse_patch_single_kinds():
    assert build_tasks.parse_patch(MODIFY) == (["pkg/mod.py"], 0)
    assert build_tasks.parse_patch(DELETE) == (["old/gone.py"], 0)
    assert build_tasks.parse_patch(ADD) == ([], 1)
    assert build_tasks.parse_patch(RENAME) == (["src/old_name.py"], 0)


def test_header_paths_with_separator_inside_a_name():
    assert build_tasks.header_paths("diff --git a/x b/y.py b/x b/y.py") == (
        "x b/y.py",
        "x b/y.py",
    )
    assert build_tasks.header_paths("diff --git a/a.py b/b.py") == ("a.py", "b.py")


def test_select_dev_drops_verified_and_keeps_sha256_order():
    ids = [
        "astropy__a-1",
        "django__django-2",
        "psf__requests-3",
        "sympy__sympy-4",
        "pytest__x-5",
        "flask__f-6",
        "astropy__a-1",
    ]
    verified = {"django__django-2"}
    everything = ["pytest__x-5", "sympy__sympy-4", "flask__f-6", "astropy__a-1", "psf__requests-3"]
    assert build_tasks.select_dev(ids, verified) == everything
    assert build_tasks.select_dev(ids, verified, size=2) == everything[:2]
    assert build_tasks.select_dev(ids, verified, size=99) == everything
