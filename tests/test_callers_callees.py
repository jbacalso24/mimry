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
from mimry.queries import _several_text

ROOT = Path(__file__).resolve().parents[1]
NOTE = (
    "Calls are matched by name, so dynamic calls (callbacks, decorators, reflection) can be"
    " missing."
)
INFERRED = (
    "Inferred means MIMRY picked the target without an exact import to back it (a re-export,"
    " a method of an imported file, or a name match); check it before relying on it."
)

ONE = """def helper(x):
    return leaf(x)


def leaf(x):
    return x


def main():
    return helper(1)
"""

# helper is reached through the package __init__, a re-export: a guess.
TWO = """from app import helper


def helper2():
    return helper(2)
"""

INIT = """from app.one import helper
"""

DIRECT = """from app.one import helper


def direct():
    return helper(3)
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


def _edge(source: str, target: str, confidence: str) -> dict:
    return {"source": source, "target": target, "relation": "calls", "confidence": confidence}


def _graph(names: str, edges: list[dict]) -> dict:
    return {
        "nodes": [_symbol(n, f"src/{n}.py", i + 1) for i, n in enumerate(names)],
        "links": edges,
    }


def _confidence(hits: list[dict]) -> dict[str, tuple[int, str]]:
    return {hit["node"]["id"]: (hit["depth"], hit["confidence"]) for hit in hits}


def test_depth_one_takes_the_confidence_of_its_edge():
    graph = _graph("abc", [_edge("a", "b", "EXTRACTED"), _edge("a", "c", "INFERRED")])

    got = _confidence(call_relations(graph, {"a"}, "callees", 1))

    assert got == {"b": (1, "EXTRACTED"), "c": (1, "INFERRED")}


def test_extracted_edge_wins_over_inferred_for_the_same_pair_in_either_order():
    for edges in (
        [_edge("a", "b", "INFERRED"), _edge("a", "b", "EXTRACTED")],
        [_edge("a", "b", "EXTRACTED"), _edge("a", "b", "INFERRED")],
    ):
        got = _confidence(call_relations(_graph("ab", edges), {"a"}, "callees", 1))

        assert got == {"b": (1, "EXTRACTED")}


def test_deeper_result_is_inferred_unless_some_shortest_path_is_all_extracted():
    graph = _graph(
        "xyzwvpqr",
        [
            _edge("x", "y", "EXTRACTED"),
            _edge("y", "z", "EXTRACTED"),
            _edge("x", "w", "INFERRED"),
            _edge("w", "v", "EXTRACTED"),
            _edge("x", "p", "EXTRACTED"),
            _edge("p", "q", "INFERRED"),
            _edge("q", "r", "EXTRACTED"),
        ],
    )

    got = _confidence(call_relations(graph, {"x"}, "callees", 3))

    assert got == {
        "y": (1, "EXTRACTED"),
        "w": (1, "INFERRED"),
        "p": (1, "EXTRACTED"),
        "z": (2, "EXTRACTED"),
        "v": (2, "INFERRED"),
        "q": (2, "INFERRED"),
        "r": (3, "INFERRED"),
    }


def test_an_all_extracted_path_of_the_same_length_wins_at_depth_two():
    graph = _graph(
        "xyzq",
        [
            _edge("x", "q", "INFERRED"),
            _edge("q", "z", "EXTRACTED"),
            _edge("x", "y", "EXTRACTED"),
            _edge("y", "z", "EXTRACTED"),
        ],
    )

    got = _confidence(call_relations(graph, {"x"}, "callees", 2))

    assert got == {"y": (1, "EXTRACTED"), "q": (1, "INFERRED"), "z": (2, "EXTRACTED")}


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
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
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def _repo(tmp_path: Path, cache: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "one.py").write_text(ONE, encoding="utf-8")
    (repo / "app" / "two.py").write_text(TWO, encoding="utf-8")
    (repo / "app" / "__init__.py").write_text(INIT, encoding="utf-8")
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
    assert "app/one.py" in res.stdout and "main" in res.stdout and "line 9" in res.stdout
    assert "app/two.py" in res.stdout and "helper2 (inferred)" in res.stdout
    assert "line 4" in res.stdout
    assert "main (inferred)" not in res.stdout
    assert res.stdout.rstrip().endswith(f"{NOTE} {INFERRED}")


def test_callees_lists_what_the_function_calls(tmp_path):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache)

    res = _mimry(repo, cache, "callees", "helper")

    assert res.returncode == 0, res.stderr
    assert "1 callee of helper (app/one.py:1)" in res.stdout
    assert "leaf" in res.stdout and "line 5" in res.stdout


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

    callers = _mimry(repo, cache, "callers", "main").stdout
    callees = _mimry(repo, cache, "callees", "leaf").stdout

    assert "No static callers of main found." in callers
    assert "search the code before treating it as unused" in callers
    assert "Nothing in the graph" not in callers
    assert "No static callees of leaf found." in callees


def test_unknown_name_says_so_and_suggests_similar_names(tmp_path):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache)

    res = _mimry(repo, cache, "callers", "Help")

    assert res.returncode == 1, res.stderr
    assert 'No function or method named "Help" is in the graph.' in res.stdout
    assert "Did you mean:" in res.stdout
    assert "helper2" in res.stdout and "app/two.py:4" in res.stdout


def test_a_name_missing_from_the_graph_exits_1_for_every_lookup(tmp_path):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache)

    for args in (
        ("symbol", "nope_missing"),
        ("callers", "nope_missing"),
        ("callees", "nope_missing"),
        ("callers", "helper", "--file", "nope.py"),
    ):
        res = _mimry(repo, cache, *args)

        assert res.returncode == 1, (args, res.stdout, res.stderr)
        assert "No " in res.stdout or "is not in the index" in res.stdout


def test_unindexed_file_suggests_close_indexed_paths(tmp_path):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache)

    text = _mimry(repo, cache, "callers", "helper", "--file", "app/oen.py")
    payload = json.loads(
        _mimry(repo, cache, "callers", "helper", "--file", "app/oen.py", "--json").stdout
    )

    assert text.returncode == 1, text.stderr
    assert "app/oen.py is not in the index." in text.stdout
    assert "Did you mean:" in text.stdout and "app/one.py" in text.stdout
    assert "helper2" not in text.stdout
    assert payload["error"]["code"] == "unknown_file"
    assert "app/one.py" in payload["error"]["suggestions"]


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
    assert "No static callees of helper found." in by_suffix.stdout


@pytest.mark.parametrize(
    "spelling",
    ["app/one.py", "./app/one.py", "app\\one.py", "app/../app/one.py", "{abs}"],
)
def test_file_filter_accepts_dot_slash_backslash_and_absolute_paths(tmp_path, spelling):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache)
    _add_second_helper(repo)
    spelling = spelling.replace("{abs}", str(repo / "app" / "one.py"))

    payload = json.loads(
        _mimry(repo, cache, "callers", "helper", "--file", spelling, "--json").stdout
    )

    assert payload["target"]["path"] == "app/one.py"
    assert payload["ambiguous"] is False
    assert payload["file_matched"] is True


def test_file_filter_that_matches_no_file_says_so(tmp_path):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache)
    _add_second_helper(repo)

    payload = json.loads(
        _mimry(repo, cache, "callers", "helper", "--file", "nope.py", "--json").stdout
    )
    text = _mimry(repo, cache, "callers", "helper", "--file", "nope.py")

    assert payload["target"] is None
    assert payload["ambiguous"] is False
    assert payload["file_matched"] is False
    assert "nope.py is not in the index." in text.stdout


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
        set(r) == {"name", "kind", "path", "line", "depth", "confidence"} and r["depth"] == 1
        for r in payload["results"]
    )
    confidence = {r["name"]: r["confidence"] for r in payload["results"]}
    assert confidence == {"main": "EXTRACTED", "helper2": "INFERRED"}
    assert payload["note"] == f"{NOTE} {INFERRED}"


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


def test_mcp_symbol_file_filter_accepts_a_dot_slash_path(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache)
    _add_second_helper(repo)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))

    payload = mcp_tools.mimry_symbol("helper", str(repo), relations="callers", file="./app/one.py")

    assert payload["target"]["path"] == "app/one.py"
    assert payload["file_matched"] is True


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
        "mimry.queries.index_and_graph_health",
        lambda root, ptr: ({}, {"status": "stale"}),
    )

    payload = mcp_tools.mimry_symbol("helper", str(repo), relations="callers")

    assert payload["error"]["code"] == "graph_not_current"
    assert payload["results"] == []


CORE_INIT = """from app.core import base
"""

CORE_PY = """def base():
    return 1


def mid():
    return base()


def top():
    return mid()
"""

# base reaches wrap through the package __init__, a re-export: a guess.
USE_PY = """from app import base


def wrap():
    return base()


def outer():
    return wrap()
"""


def test_callees_marks_the_inferred_callee_only(tmp_path):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache)

    inferred = _mimry(repo, cache, "callees", "helper2")
    plain = _mimry(repo, cache, "callees", "helper")

    assert inferred.returncode == 0, inferred.stderr
    assert "helper (inferred)" in inferred.stdout
    assert inferred.stdout.rstrip().endswith(f"{NOTE} {INFERRED}")
    assert "leaf" in plain.stdout and "(inferred)" not in plain.stdout
    assert plain.stdout.rstrip().endswith(NOTE)


def test_note_about_inference_only_appears_when_an_inferred_result_is_shown(tmp_path):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache)

    text = _mimry(repo, cache, "callers", "leaf").stdout

    assert "helper" in text and "(inferred)" not in text and "Inferred means" not in text
    assert text.rstrip().endswith(NOTE)


def test_depth_two_marks_results_reached_through_an_inferred_hop(tmp_path):
    cache = tmp_path / "cache"
    repo = _indexed_repo(
        tmp_path,
        cache,
        {"app/__init__.py": CORE_INIT, "app/core.py": CORE_PY, "app/use.py": USE_PY},
    )

    callers = json.loads(_mimry(repo, cache, "callers", "base", "--depth", "2", "--json").stdout)
    callees = json.loads(_mimry(repo, cache, "callees", "outer", "--depth", "2", "--json").stdout)
    top = json.loads(_mimry(repo, cache, "callees", "top", "--depth", "2", "--json").stdout)

    def by_name(payload):
        return {r["name"]: (r["depth"], r["confidence"]) for r in payload["results"]}

    assert by_name(callers) == {
        "mid": (1, "EXTRACTED"),
        "wrap": (1, "INFERRED"),
        "top": (2, "EXTRACTED"),
        "outer": (2, "INFERRED"),
    }
    assert by_name(callees) == {"wrap": (1, "EXTRACTED"), "base": (2, "INFERRED")}
    assert by_name(top) == {"mid": (1, "EXTRACTED"), "base": (2, "EXTRACTED")}


def test_direct_exact_import_is_extracted_and_reexport_is_inferred(tmp_path):
    cache = tmp_path / "cache"
    repo = _indexed_repo(
        tmp_path,
        cache,
        {
            "app/one.py": ONE,
            "app/__init__.py": INIT,
            "app/two.py": TWO,
            "app/direct.py": DIRECT,
        },
    )

    payload = json.loads(_mimry(repo, cache, "callers", "helper", "--json").stdout)

    assert {r["name"]: r["confidence"] for r in payload["results"]} == {
        "main": "EXTRACTED",
        "helper2": "INFERRED",
        "direct": "EXTRACTED",
    }
    text = _mimry(repo, cache, "callers", "helper").stdout
    assert "direct (inferred)" not in text and "helper2 (inferred)" in text


def test_path_marks_an_inferred_step_and_json_carries_confidence(tmp_path):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache)

    text = _mimry(repo, cache, "path", "helper2", "helper")
    payload = json.loads(_mimry(repo, cache, "path", "helper2", "helper", "--json").stdout)
    extracted = _mimry(repo, cache, "path", "helper", "leaf")

    assert text.returncode == 0, text.stderr
    assert "calls (inferred)" in text.stdout
    assert text.stdout.rstrip().endswith(INFERRED)
    assert payload["found"] is True
    assert payload["steps"] == [
        {"from": "helper2", "relation": "calls", "to": "helper", "confidence": "INFERRED"}
    ]
    assert "(inferred)" not in extracted.stdout and "Inferred means" not in extracted.stdout


def test_path_puts_each_relation_between_the_two_nodes_it_joins(tmp_path):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache)

    two = _mimry(repo, cache, "path", "helper2", "leaf")
    one = _mimry(repo, cache, "path", "helper2", "helper")

    assert two.returncode == 0, two.stderr
    assert two.stdout.splitlines()[0] == "OK helper2 connects to leaf in 2 steps"
    assert two.stdout.split("\n\n", 1)[1].splitlines()[:5] == [
        "  helper2 (app/two.py)",
        "    calls (inferred)",
        "  helper (app/one.py)",
        "    calls",
        "  leaf (app/one.py)",
    ]
    assert one.stdout.split("\n\n", 1)[1].splitlines()[:3] == [
        "  helper2 (app/two.py)",
        "    calls (inferred)",
        "  helper (app/one.py)",
    ]
    assert one.stdout.rstrip().endswith(INFERRED)


def test_path_says_when_a_name_matches_several_symbols(tmp_path):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache)
    _add_second_helper(repo)

    res = _mimry(repo, cache, "path", "helper", "leaf")
    payload = json.loads(_mimry(repo, cache, "path", "helper", "leaf", "--json").stdout)
    picked = _mimry(repo, cache, "path", "app/one.py", "leaf")
    picked_payload = json.loads(_mimry(repo, cache, "path", "app/one.py", "leaf", "--json").stdout)

    assert res.returncode == 0, res.stderr
    lines = res.stdout.splitlines()
    ok = lines.index("OK helper connects to leaf in 1 step")
    assert lines[ok + 1] == (
        '  Several symbols are named "helper": app/one.py, app/three.py. Using app/one.py;'
        " give a file path to pick another."
    )
    assert payload["found"] is True
    assert payload["ambiguous"] == {"source": ["app/three.py"], "target": []}
    assert "Several symbols" not in picked.stdout
    assert "ambiguous" not in picked_payload


def test_several_names_list_at_most_five_paths(tmp_path):
    candidates = [
        {"id": f"s{i}", "label": "x", "path": f"pkg/m{i}.py", "score": 1, "node": {}}
        for i in range(7)
    ]
    text = _several_text("x", {"candidates": candidates, "used": "s3"})

    assert text == (
        'Several symbols are named "x": pkg/m0.py, pkg/m1.py, pkg/m2.py, pkg/m3.py,'
        " pkg/m4.py, and 2 more. Using pkg/m3.py; give a file path to pick another."
    )


def test_mcp_symbol_and_path_carry_confidence(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    repo = _repo(tmp_path, cache)

    symbol = mcp_tools.mimry_symbol("helper", str(repo), relations="callers")
    path = mcp_tools.mimry_path("helper2", "helper", str(repo))

    assert {r["name"]: r["confidence"] for r in symbol["results"]} == {
        "main": "EXTRACTED",
        "helper2": "INFERRED",
    }
    assert path["returncode"] == 0
    assert path["steps"] == [
        {"from": "helper2", "relation": "calls", "to": "helper", "confidence": "INFERRED"}
    ]
    assert "calls (inferred)" in path["stdout"]


PKG_SERVICE = """def create(x):
    return dict(x)


def batch_swipe(items):
    return [create(i) for i in items]
"""

PKG_VIEWS = """from pkg import service


def create_view(x):
    return service.create(x)


def swipe_view(x):
    return service.batch_swipe(x)
"""

PKG_SIBLING = """from . import service


def sibling_view(x):
    return service.create(x)
"""

PKG_OTHER = """def create(x):
    return x
"""

PKG_PICKY = """from pkg import other, service


def both(x):
    return service.create(x)
"""


def _indexed_repo(tmp_path: Path, cache: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    for rel_path, text in files.items():
        path = repo / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "MIMRY Test")
    _git(repo, "config", "user.email", "test@example.com")
    assert _mimry(repo, cache, "init").returncode == 0
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Initial commit")
    assert _mimry(repo, cache, "index").returncode == 0
    return repo


def test_module_qualified_calls_reach_the_defining_submodule(tmp_path):
    """Module-qualified calls reach the submodule that defines them."""
    cache = tmp_path / "cache"
    repo = _indexed_repo(
        tmp_path,
        cache,
        {
            "pkg/__init__.py": "",
            "pkg/service.py": PKG_SERVICE,
            "pkg/views.py": PKG_VIEWS,
            "pkg/sibling.py": PKG_SIBLING,
        },
    )

    create = _mimry(repo, cache, "callers", "create", "--file", "pkg/service.py")
    swipe = _mimry(repo, cache, "callers", "batch_swipe", "--file", "pkg/service.py")

    assert create.returncode == 0, create.stderr
    assert "create_view" in create.stdout and "pkg/views.py" in create.stdout
    assert "sibling_view" in create.stdout and "pkg/sibling.py" in create.stdout
    assert "swipe_view" in swipe.stdout and "pkg/views.py" in swipe.stdout


def test_qualifier_picks_the_module_named_by_the_receiver(tmp_path):
    """A receiver naming a module breaks a tie between two files."""
    cache = tmp_path / "cache"
    repo = _indexed_repo(
        tmp_path,
        cache,
        {
            "pkg/__init__.py": "",
            "pkg/service.py": PKG_SERVICE,
            "pkg/other.py": PKG_OTHER,
            "pkg/picky.py": PKG_PICKY,
        },
    )

    service_callers = _mimry(repo, cache, "callers", "create", "--file", "pkg/service.py")
    other_callers = _mimry(repo, cache, "callers", "create", "--file", "pkg/other.py")

    assert "both" in service_callers.stdout and "pkg/picky.py" in service_callers.stdout
    assert "pkg/picky.py" not in other_callers.stdout


TS_ROOT_CONFIG = """{
  // Root config: "@/" points into src.
  "compilerOptions": {
    "baseUrl": ".",
    /* paths are relative to baseUrl */
    "paths": {
      "@/*": ["src/*"],
    },
  },
}
"""

TS_URLS = """export function normalizeJobUrl(url: string): string {
  return url.trim();
}
"""

TS_APP = """import { normalizeJobUrl } from "@/lib/urls";

export function run(url: string) {
  return normalizeJobUrl(url);
}
"""

TS_SUB_PAGE = """import { normalizeJobUrl } from "@/lib/urls";

export function page(url) {
  return normalizeJobUrl(url);
}
"""


def test_tsconfig_path_alias_reaches_the_imported_file(tmp_path):
    """tsconfig aliases with comments, extends and a broken config."""
    cache = tmp_path / "cache"
    repo = _indexed_repo(
        tmp_path,
        cache,
        {
            "tsconfig.json": TS_ROOT_CONFIG,
            "src/lib/urls.ts": TS_URLS,
            "src/app.ts": TS_APP,
            "sub/jsconfig.json": '{"extends": "../tsconfig.json"}\n',
            "sub/page.js": TS_SUB_PAGE,
            "broken/tsconfig.json": '{ "compilerOptions": { "paths": { \n',
            "broken/page.ts": TS_APP,
        },
    )

    res = _mimry(repo, cache, "callers", "normalizeJobUrl")

    assert res.returncode == 0, res.stderr
    assert "Error" not in res.stderr
    assert "src/app.ts" in res.stdout
    assert "sub/page.js" in res.stdout
    assert "broken/page.ts" in res.stdout


def test_strip_jsonc_keeps_comment_markers_inside_strings():
    import json

    from mimry.core.tsconfig import strip_jsonc

    text = '{"url": "http://x.test//a", /* note */ "list": [1, 2,], // tail\n}'

    assert json.loads(strip_jsonc(text)) == {"url": "http://x.test//a", "list": [1, 2]}


def test_extends_cycle_terminates_and_unparsable_config_is_none():
    from mimry.core.tsconfig import build_ts_paths, parse_tsconfig

    configs = {
        "a/tsconfig.json": parse_tsconfig('{"extends": "../b/tsconfig.json"}'),
        "b/tsconfig.json": parse_tsconfig('{"extends": "../a/tsconfig.json"}'),
    }
    table = build_ts_paths(configs, {"a/x.ts", "b/x.ts"})

    assert set(table) == {"a", "b"}
    assert parse_tsconfig("{ nope") is None


# Module function ReadInConfig starts on line 1, the method on line 10.
# reload calls the method; use_function calls the module function.
VIPER = """def ReadInConfig():
    return load_defaults()


def load_defaults():
    return {}


class Viper:
    def ReadInConfig(self):
        return load_defaults()

    def reload(self):
        return self.ReadInConfig()


def use_function():
    return ReadInConfig()
"""


def _viper_repo(tmp_path: Path, cache: Path) -> Path:
    repo = _repo(tmp_path, cache)
    (repo / "pkg").mkdir()
    (repo / "pkg" / "config.py").write_text(VIPER, encoding="utf-8")
    return repo


def test_same_name_twice_in_one_file_asks_for_a_line(tmp_path):
    cache = tmp_path / "cache"
    repo = _viper_repo(tmp_path, cache)

    res = _mimry(repo, cache, "callers", "ReadInConfig")

    assert res.returncode == 0, res.stderr
    assert (
        'Several symbols are named "ReadInConfig"; add --line N (or --file <path>:N) to pick one.'
        in res.stdout
    )
    assert "--file <path> to pick" not in res.stdout
    assert "pkg/config.py:1 " in res.stdout and "pkg/config.py:10 " in res.stdout


def test_line_picks_the_definition_for_callers_callees_and_impact(tmp_path):
    cache = tmp_path / "cache"
    repo = _viper_repo(tmp_path, cache)

    module = _mimry(repo, cache, "callers", "ReadInConfig", "--line", "1")
    method = _mimry(repo, cache, "callers", "ReadInConfig", "--line", "10")
    assert module.returncode == 0, module.stderr
    assert "1 caller of ReadInConfig (pkg/config.py:1)" in module.stdout
    assert "use_function" in module.stdout and "reload" not in module.stdout
    assert "1 caller of ReadInConfig (pkg/config.py:10)" in method.stdout
    assert "reload" in method.stdout and "use_function" not in method.stdout

    callees = _mimry(repo, cache, "callees", "ReadInConfig", "--line", "10")
    assert callees.returncode == 0, callees.stderr
    assert "callee of ReadInConfig (pkg/config.py:10)" in callees.stdout

    impact = _mimry(repo, cache, "impact", "ReadInConfig", "--line", "10")
    assert impact.returncode == 0, impact.stderr
    assert "Targets (1)\n  ReadInConfig (pkg/config.py:10)" in impact.stdout
    assert "reload (pkg/config.py:13)" in impact.stdout
    assert "use_function" not in impact.stdout


def test_a_line_inside_a_span_picks_the_symbol_it_belongs_to(tmp_path):
    cache = tmp_path / "cache"
    repo = _viper_repo(tmp_path, cache)

    # Line 11 is the method's return, not where the method starts.
    res = _mimry(repo, cache, "callers", "ReadInConfig", "--line", "11", "--json")

    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload["target"]["line"] == 10
    assert [r["name"] for r in payload["results"]] == ["reload"]


def test_file_with_a_trailing_line_works_like_line(tmp_path):
    cache = tmp_path / "cache"
    repo = _viper_repo(tmp_path, cache)

    by_flag = json.loads(
        _mimry(repo, cache, "callers", "ReadInConfig", "--line", "10", "--json").stdout
    )
    by_suffix = json.loads(
        _mimry(
            repo, cache, "callers", "ReadInConfig", "--file", "./pkg/config.py:10", "--json"
        ).stdout
    )

    assert by_suffix["target"] == by_flag["target"]
    assert by_suffix["results"] == by_flag["results"]
    assert by_suffix["target"]["line"] == 10


def test_a_line_that_matches_neither_lists_the_candidates_and_exits_1(tmp_path):
    cache = tmp_path / "cache"
    repo = _viper_repo(tmp_path, cache)

    callers = _mimry(repo, cache, "callers", "ReadInConfig", "--line", "5")
    impact = _mimry(repo, cache, "impact", "ReadInConfig", "--line", "99")

    assert callers.returncode == 1, callers.stderr
    assert 'No symbol named "ReadInConfig" starts at or spans line 5.' in callers.stdout
    assert "Candidates:" in callers.stdout
    assert "pkg/config.py:1 " in callers.stdout and "pkg/config.py:10 " in callers.stdout
    assert impact.returncode == 1, impact.stderr
    assert "pkg/config.py:10 " in impact.stdout


def test_candidates_in_different_files_still_suggest_file(tmp_path):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache)
    _add_second_helper(repo)

    res = _mimry(repo, cache, "callers", "helper")

    assert 'Several symbols are named "helper"; add --file <path> to pick one.' in res.stdout
    assert "--line" not in res.stdout


def test_mcp_symbol_and_impact_accept_line(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    repo = _viper_repo(tmp_path, cache)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))

    callers = mcp_tools.mimry_symbol("ReadInConfig", str(repo), relations="callers", line=10)
    by_suffix = mcp_tools.mimry_symbol(
        "ReadInConfig", str(repo), relations="callers", file="pkg/config.py:10"
    )
    impact = mcp_tools.mimry_impact("ReadInConfig", root=str(repo), line=10)
    bad = mcp_tools.mimry_symbol("ReadInConfig", str(repo), relations="callers", line=0)

    assert callers["target"]["line"] == 10
    assert [r["name"] for r in callers["results"]] == ["reload"]
    assert by_suffix["target"] == callers["target"]
    assert [t["line"] for t in impact["targets"]] == [10]
    assert bad["returncode"] == 2 and bad["error"]["code"] == "invalid_line"
    assert input_schema(mcp_tools.mimry_symbol)["properties"]["line"]["default"] is None
    assert "line" in input_schema(mcp_tools.mimry_impact)["properties"]


def test_split_file_line_and_ambiguity_hints():
    from mimry.core.artifacts import ambiguity_message, split_file_line

    assert split_file_line("app/one.py:12", None) == ("app/one.py", 12)
    assert split_file_line("app/one.py:12", 3) == ("app/one.py", 3)
    assert split_file_line("app/one.py", None) == ("app/one.py", None)
    assert split_file_line("C:/repo/one.py", None) == ("C:/repo/one.py", None)
    assert split_file_line(None, 4) == (None, 4)

    one = [{"path": "a.py", "line": 1}, {"path": "b.py", "line": 2}]
    same = [{"path": "a.py", "line": 1}, {"path": "a.py", "line": 2}]
    mixed = [*one, {"path": "a.py", "line": 9}]
    assert ambiguity_message("f", one).endswith("add --file <path> to pick one.")
    assert ambiguity_message("f", same).endswith("add --line N (or --file <path>:N) to pick one.")
    assert "--line N" in ambiguity_message("f", mixed)


GROOVY_HELPERS = "package demo\n\nclass Helpers {\n    static String util() { 'x' }\n}\n"
GROOVY_CONTROLLER = (
    "package demo\n"
    "import demo.Helpers\n"
    "\n"
    "class CallController extends Base implements Shape {\n"
    "    def helper() { 1 }\n"
    "\n"
    "    def use() {\n"
    "        helper()\n"
    "        Helpers.util()\n"
    "        this.helper()\n"
    "    }\n"
    "\n"
    "    double area() { 0 }\n"
    "}\n"
)


def _groovy_repo(tmp_path: Path, cache: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src/main/groovy/demo").mkdir(parents=True)
    (repo / "src/main/groovy/demo/Helpers.groovy").write_text(GROOVY_HELPERS, encoding="utf-8")
    (repo / "src/main/groovy/demo/CallController.groovy").write_text(
        GROOVY_CONTROLLER, encoding="utf-8"
    )
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "MIMRY Test")
    _git(repo, "config", "user.email", "test@example.com")
    assert _mimry(repo, cache, "init").returncode == 0
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Initial commit")
    assert _mimry(repo, cache, "index").returncode == 0
    return repo


def test_callers_of_a_groovy_static_method_finds_the_calling_method(tmp_path):
    cache = tmp_path / "cache"
    repo = _groovy_repo(tmp_path, cache)

    res = _mimry(repo, cache, "callers", "util")

    assert res.returncode == 0, res.stderr
    assert "1 caller of util (src/main/groovy/demo/Helpers.groovy:4)" in res.stdout
    assert "src/main/groovy/demo/CallController.groovy" in res.stdout
    assert "use (inferred)  method  line 7" in res.stdout
