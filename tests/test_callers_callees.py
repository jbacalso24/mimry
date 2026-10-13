from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mimry import mcp_tools
from mimry.core.artifacts import call_lookup, call_relations
from mimry.mcp_protocol import input_schema

ROOT = Path(__file__).resolve().parents[1]
NOTE = (
    "Calls are matched by name, so dynamic calls (callbacks, decorators, reflection) can be"
    " missing."
)

ONE = """def helper(x):
    return leaf(x)


def leaf(x):
    return x


def main():
    return helper(1)
"""

TWO = """from app.one import helper


def helper2():
    return helper(2)
"""


def _symbol(name: str, path: str, line: int) -> dict:
    return {
        "id": name,
        "label": name,
        "type": "symbol",
        "kind": "function",
        "source_file": path,
        "line": line,
    }


def _call(source: str, target: str) -> dict:
    return {"source": source, "target": target, "relation": "calls"}


def _ids(hits: list[dict]) -> dict[str, int]:
    return {hit["node"]["id"]: hit["depth"] for hit in hits}


# Chain a->b->c, a->c, d->b, cycle c->a. d->a is an import, not a call.
CHAIN = {
    "nodes": [_symbol(n, f"src/{n}.py", i + 1) for i, n in enumerate("abcd")],
    "links": [
        _call("a", "b"),
        _call("b", "c"),
        _call("a", "c"),
        _call("d", "b"),
        _call("c", "a"),
        {"source": "d", "target": "a", "relation": "imports"},
    ],
}


def test_callees_follow_calls_forward_and_stop_at_the_cycle():
    assert _ids(call_relations(CHAIN, {"a"}, "callees", 1)) == {"b": 1, "c": 1}
    assert _ids(call_relations(CHAIN, {"a"}, "callees", 2)) == {"b": 1, "c": 1}
    assert _ids(call_relations(CHAIN, {"d"}, "callees", 3)) == {"b": 1, "c": 2, "a": 3}


def test_callers_walk_backwards_by_depth_without_revisits():
    assert _ids(call_relations(CHAIN, {"a"}, "callers", 1)) == {"c": 1}
    assert _ids(call_relations(CHAIN, {"a"}, "callers", 2)) == {"c": 1, "b": 2}
    deep = call_relations(CHAIN, {"a"}, "callers", 3)
    assert _ids(deep) == {"c": 1, "b": 2, "d": 3}
    assert len(deep) == 3


def test_target_is_never_reported_and_imports_are_ignored():
    assert "a" not in _ids(call_relations(CHAIN, {"a"}, "callers", 3))
    assert "a" not in _ids(call_relations(CHAIN, {"a"}, "callees", 3))
    assert "d" not in _ids(call_relations(CHAIN, {"a"}, "callers", 1))


def test_callers_of_b_include_both_direct_callers():
    assert _ids(call_relations(CHAIN, {"b"}, "callers", 1)) == {"a": 1, "d": 1}
    assert _ids(call_relations(CHAIN, {"b"}, "callers", 2)) == {"a": 1, "d": 1, "c": 2}


def test_unknown_direction_is_rejected():
    with pytest.raises(ValueError):
        call_relations(CHAIN, {"a"}, "imports", 1)


def test_lookup_orders_files_by_result_count_then_depth_then_line():
    graph = {
        "nodes": [
            _symbol("helper", "src/x.py", 3),
            _symbol("p1", "src/a.py", 1),
            _symbol("p2", "src/b.py", 9),
            _symbol("p3", "src/b.py", 2),
            _symbol("p4", "src/b.py", 5),
        ],
        "links": [_call("p1", "helper"), _call("p2", "helper"), _call("p3", "helper")]
        + [_call("p4", "p3")],
    }

    payload = call_lookup(graph, "helper", "callers", 2)

    assert payload["target"] == {
        "name": "helper",
        "kind": "function",
        "path": "src/x.py",
        "line": 3,
    }
    # src/b.py holds three results, src/a.py one. Inside b.py, depth 1
    # comes before depth 2, and lines ascend within a depth.
    assert [(r["name"], r["path"], r["depth"]) for r in payload["results"]] == [
        ("p3", "src/b.py", 1),
        ("p2", "src/b.py", 1),
        ("p4", "src/b.py", 2),
        ("p1", "src/a.py", 1),
    ]


def test_lookup_reports_ambiguous_names_and_narrows_by_file():
    graph = {
        "nodes": [_symbol("helper", "src/x.py", 3), _symbol("helper", "src/y.py", 7)],
        "links": [],
    }

    ambiguous = call_lookup(graph, "helper", "callers", 1)
    assert ambiguous["target"] is None
    assert ambiguous["ambiguous"] is True
    assert [c["path"] for c in ambiguous["candidates"]] == ["src/x.py", "src/y.py"]

    picked = call_lookup(graph, "helper", "callers", 1, file="src/y.py")
    assert picked["ambiguous"] is False
    assert picked["target"]["path"] == "src/y.py"
    assert picked["results"] == []
    assert picked["note"] == NOTE


def test_lookup_suggests_case_insensitive_matches_when_nothing_matches():
    graph = {"nodes": [_symbol("Helper", "src/x.py", 3), _symbol("helper2", "src/y.py", 7)]}

    payload = call_lookup(graph, "HELPER", "callees", 1)

    assert payload["target"] is None
    assert payload["ambiguous"] is False
    assert [c["name"] for c in payload["candidates"]] == ["Helper", "helper2"]


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _mimry(cwd: Path, cache: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["MIMRY_CACHE_HOME"] = str(cache)
    return subprocess.run(
        [sys.executable, "-m", "mimry.cli", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _repo(tmp_path: Path, cache: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "one.py").write_text(ONE, encoding="utf-8")
    (repo / "app" / "two.py").write_text(TWO, encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "MIMRY Test")
    _git(repo, "config", "user.email", "test@example.com")
    assert _mimry(repo, cache, "init").returncode == 0
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Initial commit")
    assert _mimry(repo, cache, "index").returncode == 0
    return repo


def _add_second_helper(repo: Path) -> None:
    (repo / "app" / "three.py").write_text("def helper():\n    return 3\n", encoding="utf-8")


def test_callers_lists_each_caller_with_its_file_and_line(tmp_path):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache)

    res = _mimry(repo, cache, "callers", "helper")

    assert res.returncode == 0, res.stderr
    assert "2 callers of helper (app/one.py:1)" in res.stdout
    assert "app/one.py" in res.stdout and "main" in res.stdout and ":9" in res.stdout
    assert "app/two.py" in res.stdout and "helper2" in res.stdout and ":4" in res.stdout
    assert res.stdout.rstrip().endswith(NOTE)


def test_callees_lists_what_the_function_calls(tmp_path):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache)

    res = _mimry(repo, cache, "callees", "helper")

    assert res.returncode == 0, res.stderr
    assert "1 callee of helper (app/one.py:1)" in res.stdout
    assert "leaf" in res.stdout and ":5" in res.stdout


def test_depth_two_from_leaf_reaches_main(tmp_path):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache)

    res = _mimry(repo, cache, "callers", "leaf", "--depth", "2")

    assert res.returncode == 0, res.stderr
    assert "3 callers of leaf within 2 calls (app/one.py:5)" in res.stdout
    main_line = next(line for line in res.stdout.splitlines() if "main" in line)
    assert "2 calls away" in main_line


def test_zero_results_say_so(tmp_path):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache)

    assert "Nothing in the graph calls main." in _mimry(repo, cache, "callers", "main").stdout
    assert "leaf calls nothing in the graph." in _mimry(repo, cache, "callees", "leaf").stdout


def test_unknown_name_says_so_and_suggests_similar_names(tmp_path):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache)

    res = _mimry(repo, cache, "callers", "Help")

    assert res.returncode == 0, res.stderr
    assert 'No function or method named "Help" is in the graph.' in res.stdout
    assert "Did you mean:" in res.stdout
    assert "helper2" in res.stdout and "app/two.py:4" in res.stdout


def test_ambiguous_name_lists_candidates_until_file_picks_one(tmp_path):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache)
    _add_second_helper(repo)

    ambiguous = _mimry(repo, cache, "callers", "helper")

    assert ambiguous.returncode == 0, ambiguous.stderr
    assert 'Several symbols are named "helper"; add --file <path> to pick one.' in ambiguous.stdout
    assert "app/one.py:1" in ambiguous.stdout and "app/three.py:1" in ambiguous.stdout

    picked = _mimry(repo, cache, "callers", "helper", "--file", "app/one.py")
    assert picked.returncode == 0, picked.stderr
    assert "2 callers of helper (app/one.py:1)" in picked.stdout
    assert "Several symbols" not in picked.stdout

    by_suffix = _mimry(repo, cache, "callees", "helper", "--file", "three.py")
    assert "helper calls nothing in the graph." in by_suffix.stdout


def test_json_has_the_documented_shape(tmp_path):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache)

    res = _mimry(repo, cache, "callers", "helper", "--json")

    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert set(payload) == {
        "name",
        "relation",
        "depth",
        "target",
        "results",
        "candidates",
        "ambiguous",
        "note",
    }
    assert payload["name"] == "helper"
    assert payload["relation"] == "callers"
    assert payload["depth"] == 1
    assert payload["target"] == {
        "name": "helper",
        "kind": "function",
        "path": "app/one.py",
        "line": 1,
    }
    assert {r["name"] for r in payload["results"]} == {"main", "helper2"}
    assert all(
        set(r) == {"name", "kind", "path", "line", "depth"} and r["depth"] == 1
        for r in payload["results"]
    )
    assert payload["note"] == NOTE


def test_json_for_an_ambiguous_name_has_null_target_and_candidates(tmp_path):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache)
    _add_second_helper(repo)

    payload = json.loads(_mimry(repo, cache, "callees", "helper", "--json").stdout)

    assert payload["target"] is None
    assert payload["ambiguous"] is True
    assert [c["path"] for c in payload["candidates"]] == ["app/one.py", "app/three.py"]
    assert payload["results"] == []


def test_depth_outside_one_to_three_is_rejected(tmp_path):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache)

    res = _mimry(repo, cache, "callers", "helper", "--depth", "4")

    assert res.returncode == 2
    assert "invalid choice" in res.stderr


def test_mcp_symbol_returns_callers_payload(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    repo = _repo(tmp_path, cache)

    payload = mcp_tools.mimry_symbol("helper", str(repo), relations="callers")

    assert payload["relation"] == "callers"
    assert payload["root"] == str(repo)
    assert {r["name"] for r in payload["results"]} == {"main", "helper2"}
    assert payload["target"]["path"] == "app/one.py"


def test_mcp_symbol_without_relations_is_unchanged(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    repo = _repo(tmp_path, cache)

    payload = mcp_tools.mimry_symbol("helper", str(repo))

    assert set(payload) == {"name", "root", "symbols"}
    assert {s["name"] for s in payload["symbols"]} == {"helper", "helper2"}


def test_mcp_symbol_rejects_bad_relations_and_depth(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    repo = _repo(tmp_path, cache)

    bad_relation = mcp_tools.mimry_symbol("helper", str(repo), relations="imports")
    bad_depth = mcp_tools.mimry_symbol("helper", str(repo), relations="callers", depth=4)

    assert bad_relation["returncode"] == 2
    assert bad_relation["error"]["code"] == "invalid_relations"
    assert bad_depth["returncode"] == 2
    assert bad_depth["error"]["code"] == "invalid_depth"


def test_mcp_symbol_schema_lists_the_new_arguments():
    properties = input_schema(mcp_tools.mimry_symbol)["properties"]

    assert {"relations", "depth", "file"} <= set(properties)
    assert properties["depth"]["default"] == 1


def test_stale_graph_is_reported_not_answered(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    repo = _repo(tmp_path, cache)
    monkeypatch.setattr(
        "mimry.commands._index_and_graph_health",
        lambda root, ptr: ({}, {"status": "stale"}),
    )

    payload = mcp_tools.mimry_symbol("helper", str(repo), relations="callers")

    assert payload["error"]["code"] == "graph_not_current"
    assert payload["results"] == []
