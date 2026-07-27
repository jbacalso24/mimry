from __future__ import annotations

import json
from pathlib import Path

from mimry.graphify_artifacts import graphify_rows


def write_graph(root: Path) -> None:
    graph_dir = root / ".mimry" / "graphify"
    graph_dir.mkdir(parents=True)
    graph = {
        "nodes": [
            {
                "id": "doc-monitor-ams",
                "label": "AMS monitor dashboard implementation plan",
                "source_file": "README.md",
                "community": 1,
            },
            {
                "id": "docs-ams-dashboard",
                "label": "AMS monitor dashboard design docs",
                "source_file": "docs/ams-dashboard-plan.md",
                "community": 1,
            },
            {
                "id": "src-monitor-ams-dashboard",
                "label": "AMS monitor dashboard implementation",
                "source_file": "src/ams/monitor_dashboard.py",
                "community": 2,
            },
            {
                "id": "test-monitor-ams-dashboard",
                "label": "AMS monitor dashboard implementation test",
                "source_file": "tests/test_monitor_dashboard.py",
                "community": 2,
            },
        ],
        "links": [
            {"source": "doc-monitor-ams", "target": "docs-ams-dashboard", "relation": "references"},
            {"source": "src-monitor-ams-dashboard", "target": "test-monitor-ams-dashboard", "relation": "tested_by"},
            {"source": "src-monitor-ams-dashboard", "target": "doc-monitor-ams", "relation": "implements"},
        ],
    }
    (graph_dir / "graph.json").write_text(json.dumps(graph), encoding="utf-8")


def test_graphify_broad_query_can_surface_docs(tmp_path):
    write_graph(tmp_path)

    rows = graphify_rows(tmp_path, "ams", limit=4)

    paths = [r["path"] for r in rows]
    assert "README.md" in paths
    assert "docs/ams-dashboard-plan.md" in paths


def test_graphify_edit_intent_promotes_source_above_docs_and_explains(tmp_path):
    write_graph(tmp_path)

    rows = graphify_rows(tmp_path, "fix ams monitor dashboard implementation", limit=4)

    paths = [r["path"] for r in rows]
    assert paths.index("src/ams/monitor_dashboard.py") < paths.index("README.md")
    assert paths.index("src/ams/monitor_dashboard.py") < paths.index("docs/ams-dashboard-plan.md")
    source_reason = next(r["reason"] for r in rows if r["path"] == "src/ams/monitor_dashboard.py")
    doc_reason = next(r["reason"] for r in rows if r["path"] == "README.md")
    assert "intent source boost" in source_reason
    assert "doc downrank for edit intent" in doc_reason


def test_graphify_rows_consolidate_file_topology_reasons_without_changing_score(tmp_path):
    graph_dir = tmp_path / ".mimry" / "graphify"
    graph_dir.mkdir(parents=True)
    graph = {
        "nodes": [
            {"id": "first", "label": "Widget first", "source_file": "src/widgets.py", "community": 10},
            {"id": "second", "label": "Widget second", "source_file": "src/widgets.py", "community": 2},
            {"id": "helper-a", "label": "Helper A", "source_file": "src/a.py"},
            {"id": "helper-b", "label": "Helper B", "source_file": "src/b.py"},
        ],
        "links": [
            {"source": "first", "target": "helper-a"},
            {"source": "second", "target": "helper-a"},
            {"source": "second", "target": "helper-b"},
        ],
    }
    (graph_dir / "graph.json").write_text(json.dumps(graph), encoding="utf-8")

    [row] = graphify_rows(tmp_path, "widget")

    # Preserve the per-node match and topology boosts:
    # (45 label + 35 source + 5 degree + 5 community)
    # + (45 label + 35 source + 10 degree + 5 community).
    assert row["score"] == 185
    assert "Graphify max degree 2" in row["reason"]
    assert "Graphify communities 2, 10" in row["reason"]
    assert "Graphify degree 1" not in row["reason"]
    assert "Graphify degree 2" not in row["reason"]
