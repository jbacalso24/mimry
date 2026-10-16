"""Unit tests for the localization scorer and its parsers.

Covers the scorer, the patch parser and the find output parser. No
network: the dataset is never fetched here.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

LOC = Path(__file__).resolve().parents[1] / "benchmarks" / "localization"
# run.py imports build_tasks as a sibling module, so the folder must be
# on the path first.
sys.path.insert(0, str(LOC))
import build_tasks  # noqa: E402
import partner_report  # noqa: E402


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


def test_parse_find_explained_pairs_paths_with_score_lines():
    run = _load_run()
    output = (
        "Best matches\n\n"
        "   1  a/b.py\n"
        "      name matches\n"
        "      score 120: graph node label match, intent source boost\n"
        "   2  tests/test_b.py\n"
        "      name matches\n"
    )
    assert run.parse_find_explained(output, 50) == [
        ("a/b.py", "score 120: graph node label match, intent source boost"),
        ("tests/test_b.py", None),
    ]
    diagnosis = run.diagnosis(run.parse_find_explained(output, 50), ["tests/test_b.py", "c.py"])
    assert diagnosis["gold_rank"] == {"c.py": None, "tests/test_b.py": 2}
    assert [item["path"] for item in diagnosis["explain"]] == ["a/b.py", "tests/test_b.py"]


def _graph(files, links):
    """Build a graph from node paths and (source, target, relation)."""
    return {
        "nodes": [{"id": node, "source_file": path} for node, path in files.items()],
        "links": [{"source": s, "target": t, "relation": r} for s, t, r in links],
    }


PARTNER_GRAPH = _graph(
    {
        "found": "pkg/found.py",
        "m1": "pkg/m1.py",
        "m1b": "pkg/m1.py",
        "m2": "pkg/m2.py",
        "m3": "pkg/m3.py",
        "m4": "pkg/m4.py",
        "m5": "pkg/m5.py",
        "m5b": "pkg/m5.py",
        "m6": "pkg/m6.py",
        "lone": "far/lone.py",
        "top2": "x/top2.py",
    },
    [
        ("m1", "found", "calls"),
        ("m2", "m1", "imports"),
        ("m3", "m2", "imports"),
        ("m4", "m3", "imports"),
        ("found", "m6", "imports"),
        ("m1b", "m1", "defines"),
        ("m5", "m5b", "contains"),
    ],
)
PARTNER_TOP = ["pkg/found.py", "x/top2.py"]
PARTNER_GOLD = [
    "pkg/found.py",
    "pkg/m1.py",
    "pkg/m2.py",
    "pkg/m3.py",
    "pkg/m4.py",
    "pkg/m5.py",
    "pkg/m6.py",
    "far/lone.py",
]
PARTNER_RANK = {
    "pkg/found.py": 1,
    "pkg/m1.py": 11,
    "pkg/m2.py": 12,
    "pkg/m3.py": 13,
    "pkg/m4.py": 14,
    "pkg/m5.py": None,
    "pkg/m6.py": 15,
    "far/lone.py": None,
}


def _partners():
    return run.partner_links(PARTNER_GRAPH, PARTNER_GOLD, PARTNER_TOP, PARTNER_RANK)


def test_partner_one_hop_records_relation_and_direction():
    m1 = _partners()["pkg/m1.py"]
    assert m1["rank"] == 11
    assert m1["hops_to_found_gold"] == 1
    assert m1["hops_to_top10"] == 1
    assert m1["direct"] == [{"file": "pkg/found.py", "relations": ["calls"], "direction": "out"}]
    m6 = _partners()["pkg/m6.py"]
    assert m6["direct"] == [{"file": "pkg/found.py", "relations": ["imports"], "direction": "in"}]


def test_partner_two_and_three_hops():
    partners = _partners()
    assert partners["pkg/m2.py"]["hops_to_found_gold"] == 2
    assert partners["pkg/m2.py"]["direct"] == []
    assert partners["pkg/m3.py"]["hops_to_found_gold"] == 3
    assert partners["pkg/m3.py"]["hops_to_top10"] == 3


def test_partner_beyond_three_hops_is_null():
    m4 = _partners()["pkg/m4.py"]
    assert m4["hops_to_found_gold"] is None
    assert m4["hops_to_top10"] is None
    assert m4["rank"] == 14


def test_partner_not_connected_is_null():
    lone = _partners()["far/lone.py"]
    assert lone == {
        "rank": None,
        "hops_to_found_gold": None,
        "hops_to_top10": None,
        "direct": [],
        "same_dir_as_found_gold": False,
    }


def test_partner_no_gold_in_top_ten_still_reaches_top():
    graph = _graph({"m1": "pkg/m1.py", "top2": "x/top2.py"}, [("m1", "top2", "calls")])
    m1 = run.partner_links(graph, ["pkg/m1.py"], ["x/top2.py"], {"pkg/m1.py": 12})["pkg/m1.py"]
    assert m1["hops_to_found_gold"] is None
    assert m1["hops_to_top10"] == 1
    assert m1["same_dir_as_found_gold"] is False


def test_partner_same_folder_flag():
    partners = _partners()
    assert partners["pkg/m1.py"]["same_dir_as_found_gold"] is True
    assert partners["pkg/m4.py"]["same_dir_as_found_gold"] is True
    assert partners["far/lone.py"]["same_dir_as_found_gold"] is False


def test_partner_edges_inside_one_file_ignored():
    m5 = _partners()["pkg/m5.py"]
    assert m5["direct"] == []
    assert m5["hops_to_found_gold"] is None
    assert m5["hops_to_top10"] is None


def _node(node_id, label, path):
    return {"id": node_id, "label": label, "type": "symbol", "source_file": path}


FEATURE_GRAPH = {
    "nodes": [
        _node("s_found", "found_handler", "pkg/found.py"),
        _node("s_found2", "helper", "pkg/found.py"),
        _node("s_parse", "parse_request", "pkg/helpers.py"),
        _node("s_build", "BuildRequestParser", "pkg/helpers.py"),
        _node("s_test", "test_found", "pkg/found_test.py"),
        _node("s_other", "other", "other/x.py"),
    ],
    "links": [
        {"source": "s_found", "target": "s_parse", "relation": "calls"},
        {"source": "s_build", "target": "s_found", "relation": "imports"},
        {"source": "s_found", "target": "s_found2", "relation": "contains"},
        {"source": "s_test", "target": "s_found", "relation": "imports"},
        {"source": "s_found", "target": "s_other", "relation": "calls"},
    ],
}
FEATURE_EXPLAINED = [
    ("pkg/found.py", None),
    ("other/x.py", None),
    ("pkg/helpers.py", None),
    ("pkg/found_test.py", None),
]
FEATURE_GOLD = ["pkg/found.py", "pkg/helpers.py"]


def test_neighbour_features_from_a_hand_made_graph():
    records = run.neighbour_records(
        FEATURE_GRAPH, "parse request helpers", FEATURE_EXPLAINED, FEATURE_GOLD
    )
    assert [record["seed_rank"] for record in records] == [1, 2, 3]
    first = records[0]
    assert first["seed"] == "pkg/found.py" and first["seed_is_gold"]
    assert first["neighbour_total"] == 3
    by_file = {item["file"]: item for item in first["neighbours"]}
    assert sorted(by_file) == ["other/x.py", "pkg/found_test.py", "pkg/helpers.py"]
    assert by_file["pkg/helpers.py"] == {
        "seed_rank": 1,
        "seed_is_gold": True,
        "file": "pkg/helpers.py",
        "is_gold": True,
        "rank": 3,
        "relations": ["calls", "imports"],
        "direction": "both",
        "edge_count": 2,
        "linked_symbols": ["BuildRequestParser", "parse_request"],
        "linked_symbol_query_overlap": 2,
        "neighbour_degree": 1,
        "same_dir": True,
        "same_top_package": True,
        "name_stem_match": False,
        "is_test": False,
        "query_overlap_path": 1,
    }
    test_file = by_file["pkg/found_test.py"]
    assert test_file["direction"] == "in" and test_file["edge_count"] == 1
    assert test_file["linked_symbols"] == ["test_found"]
    assert test_file["linked_symbol_query_overlap"] == 0
    assert test_file["name_stem_match"] and test_file["is_test"] and test_file["same_dir"]
    assert test_file["rank"] == 4 and not test_file["is_gold"]
    other = by_file["other/x.py"]
    assert other["direction"] == "out" and other["linked_symbols"] == ["other"]
    assert not other["same_dir"] and not other["same_top_package"]
    assert not other["name_stem_match"]
    [back] = records[2]["neighbours"]
    assert back["file"] == "pkg/found.py" and back["direction"] == "both"
    assert back["seed_rank"] == 3 and back["seed_is_gold"]
    assert back["linked_symbols"] == ["found_handler"]


def test_neighbour_sample_keeps_every_gold_and_caps_the_rest():
    count = 250
    graph = {
        "nodes": [{"id": "seed", "source_file": "big/seed.py"}]
        + [{"id": f"id{i}", "source_file": f"big/n{i:03d}.py"} for i in range(count)],
        "links": [
            {"source": "seed", "target": f"id{i}", "relation": "imports"} for i in range(count)
        ],
    }
    gold = ["big/seed.py", "big/n007.py", "big/n200.py"]
    [seed] = run.neighbour_records(graph, "unrelated", [("big/seed.py", None)], gold)
    assert seed["neighbour_total"] == count
    kept = [item["file"] for item in seed["neighbours"]]
    assert "big/n007.py" in kept and "big/n200.py" in kept
    assert len(kept) == run.NEIGHBOUR_SAMPLE + 2
    assert kept == sorted(kept)
    assert "big/n000.py" in kept
    again = run.neighbour_records(graph, "unrelated", [("big/seed.py", None)], gold)
    assert again == [seed]


def test_name_stem_drops_folder_extensions_and_test_affixes():
    assert run._stem("src/serve-static/index.ts") == "index"
    assert run._stem("src/serve-static/index.test.ts") == "index"
    assert run._stem("pkg/foo.py") == run._stem("pkg/test_foo.py") == "foo"
    assert run._stem("pkg/foo_test.py") == "foo"
    assert run._stem("pkg/serve-static.ts") != run._stem("pkg/index.ts")


def test_partner_report_on_a_tiny_results_file(tmp_path):
    def neighbour(file, is_gold, **features):
        base = {
            "seed_rank": 1,
            "seed_is_gold": True,
            "file": file,
            "is_gold": is_gold,
            "rank": None,
            "relations": ["imports"],
            "direction": "out",
            "edge_count": 1,
            "linked_symbols": [],
            "linked_symbol_query_overlap": 0,
            "neighbour_degree": 1,
            "same_dir": False,
            "same_top_package": False,
            "name_stem_match": False,
            "is_test": False,
            "query_overlap_path": 0,
        }
        return base | features

    seed = {
        "seed_rank": 1,
        "seed": "a/g.py",
        "seed_is_gold": True,
        "neighbour_total": 3,
        "neighbours": [
            neighbour("b/m.py", True, edge_count=2, same_dir=True, rank=12),
            neighbour("c/o.py", False, direction="in", name_stem_match=True),
        ],
    }
    instance = {
        "instance_id": "x-1",
        "status": "scored",
        "gold": ["a/g.py", "b/m.py"],
        "diagnosis": {"gold_rank": {"a/g.py": 1, "b/m.py": 12}, "neighbours": [seed]},
    }
    path = tmp_path / "results.json"
    path.write_text(json.dumps({"instances": [instance], "summary": {}}), encoding="utf-8")
    text = partner_report.partner_features_report(path)
    assert "Missing gold partners: 1." in text
    # One kept non-gold neighbour stands for 2 of the 3 neighbours.
    assert "| any neighbour | 3 | 1 | 33.3% | 1 of 1 (100.0%) |" in text
    assert "| edge_count >= 2 | 1 | 1 | 100.0% | 1 of 1 (100.0%) |" in text
    assert "| name_stem_match | 2 | 0 | 0.0% | 0 of 1 (0.0%) |" in text
    assert "| edge_count >= 3 | 0 | 0 | n/a | 0 of 1 (0.0%) |" in text
