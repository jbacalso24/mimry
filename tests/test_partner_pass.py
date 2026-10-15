from __future__ import annotations

import json

from mimry import search
from mimry.search import find_rows
from mimry.storage import write_jsonl

# The anchor is matched by the graph alone, so the graph path runs.
# Views is matched by the content index only, so its neighbours come
# from the partner pass, not from graph expansion.
ANCHOR = "pkg/anchor.py"


def _build(tmp_path, hints, links):
    """Index files with content hints, and graph edges between pairs."""
    linked = sorted({path for link in links for path in link} - set(hints))
    paths = [*hints, *linked, ANCHOR]
    idx = tmp_path / "index"
    idx.mkdir()
    write_jsonl(
        idx / "files.jsonl",
        [
            {
                "file_id": path,
                "filename": path.rsplit("/", 1)[-1],
                "rel_path": path,
                "content_hint": hints.get(path, ""),
                "metadata_text": "",
                "adapter": "python-ast" if path.endswith(".py") else "generic",
            }
            for path in paths
        ],
    )
    node_ids = {path: f"node-{i}" for i, path in enumerate(paths)}
    nodes = [{"id": node_ids[path], "source_file": path} for path in paths]
    nodes[-1]["label"] = "checkout redirect"
    graph = {
        "nodes": nodes,
        "links": [
            {"source": node_ids[a], "target": node_ids[b], "relation": "imports"} for a, b in links
        ],
    }
    graph_dir = tmp_path / ".mimry" / "mimry-out" / "graph"
    graph_dir.mkdir(parents=True)
    (graph_dir / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    return idx


def _by_path(rows):
    return {row["path"]: row for row in rows}


def test_a_partner_with_no_query_word_enters_with_the_seed_reason(tmp_path):
    idx = _build(
        tmp_path,
        {"pkg/views.py": "checkout redirect", "pkg/service.py": "ledger billing"},
        [("pkg/views.py", "pkg/service.py")],
    )

    rows = _by_path(find_rows(idx, "checkout redirect", limit=10, root=tmp_path))

    service = rows["pkg/service.py"]
    assert service["reason"] == "partner of pkg/views.py"
    assert service["score"] == round(search.PARTNER_SHARE * rows["pkg/views.py"]["score"])


def test_partner_needing_a_query_match_stays_out(tmp_path, monkeypatch):
    monkeypatch.setattr(search, "PARTNER_NEEDS_MATCH", True)
    idx = _build(
        tmp_path,
        {"pkg/views.py": "checkout redirect", "pkg/service.py": "ledger billing"},
        [("pkg/views.py", "pkg/service.py")],
    )

    paths = [row["path"] for row in find_rows(idx, "checkout redirect", limit=10, root=tmp_path)]

    assert "pkg/service.py" not in paths
    assert "pkg/views.py" in paths


def test_a_test_partner_is_pulled_up_only_when_the_query_asks_for_tests(tmp_path):
    idx = _build(
        tmp_path,
        {"pkg/views.py": "checkout redirect", "tests/test_views.py": ""},
        [("pkg/views.py", "tests/test_views.py")],
    )

    source_rows = _by_path(find_rows(idx, "checkout redirect", limit=10, root=tmp_path))
    # "pytest" asks for tests but matches no word in the test's path.
    test_rows = _by_path(find_rows(idx, "checkout redirect pytest", limit=10, root=tmp_path))

    assert "tests/test_views.py" not in source_rows
    assert "partner of pkg/views.py" in test_rows["tests/test_views.py"]["reason"]


def test_only_the_highest_scored_partners_per_seed_are_boosted(tmp_path, monkeypatch):
    # Only views is a seed, so the matched partner below is not one.
    monkeypatch.setattr(search, "PARTNER_SEEDS", 2)
    idx = _build(
        tmp_path,
        {"pkg/views.py": "checkout redirect", "pkg/zeta.py": "invoice"},
        [
            ("pkg/views.py", "pkg/alpha.py"),
            ("pkg/views.py", "pkg/beta.py"),
            ("pkg/views.py", "pkg/zeta.py"),
        ],
    )

    rows = _by_path(find_rows(idx, "checkout redirect invoice", limit=10, root=tmp_path))

    # zeta.py sorts last by path but is the only scored partner, so it
    # takes a slot ahead of alpha.py; beta.py gets none.
    assert "partner of pkg/views.py" in rows["pkg/zeta.py"]["reason"]
    assert "partner of pkg/views.py" in rows["pkg/alpha.py"]["reason"]
    assert "pkg/beta.py" not in rows


def test_a_partner_of_two_seeds_gets_the_larger_gain_once(tmp_path):
    idx = _build(
        tmp_path,
        {"pkg/views.py": "checkout redirect", "pkg/cart.py": "checkout"},
        [("pkg/views.py", "pkg/shared.py"), ("pkg/cart.py", "pkg/shared.py")],
    )

    rows = _by_path(find_rows(idx, "checkout redirect", limit=10, root=tmp_path))

    shared = rows["pkg/shared.py"]
    assert shared["score"] == round(search.PARTNER_SHARE * rows["pkg/views.py"]["score"])
    assert shared["reason"].count("partner of") == 1
    assert "partner of pkg/views.py" in shared["reason"]


def test_a_partner_gain_never_moves_the_top_result(tmp_path, monkeypatch):
    hints = {"pkg/views.py": "checkout redirect", "pkg/service.py": "ledger billing"}
    links = [("pkg/views.py", "pkg/service.py")]
    idx = _build(tmp_path, hints, links)

    monkeypatch.setattr(search, "PARTNER_SHARE", 0)
    before = find_rows(idx, "checkout redirect", limit=10, root=tmp_path)
    monkeypatch.setattr(search, "PARTNER_SHARE", 0.5)
    after = find_rows(idx, "checkout redirect", limit=10, root=tmp_path)

    assert after[0]["path"] == before[0]["path"]
    assert after[0]["score"] == before[0]["score"]


def test_a_ranked_partner_never_overtakes_its_seed(monkeypatch):
    ranked = [
        {"path": "a.py", "score": 100, "reason": "match"},
        {"path": "b.py", "score": 90, "reason": "match"},
        {"path": "c.py", "score": 80, "reason": "match"},
        {"path": "d.py", "score": 70, "reason": "match"},
    ]
    monkeypatch.setattr(search, "file_partners", lambda _root, paths: {p: {"d.py"} for p in paths})

    out = search._partner_pass(ranked, None, {r["path"] for r in ranked}, frozenset(), False)

    assert [r["path"] for r in out] == ["a.py", "d.py", "b.py", "c.py"]
    assert out[1]["score"] == 99
    assert out[1]["reason"] == "match, partner of a.py"
