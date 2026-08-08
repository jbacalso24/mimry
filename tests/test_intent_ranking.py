from __future__ import annotations

import json
from pathlib import Path

from mimry.core.artifacts import graph_rows
from mimry.intent import is_concept_intent, is_edit_intent, query_terms


def write_graph(root: Path) -> None:
    graph_dir = root / ".mimry" / "mimry-out" / "graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
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


def test_graph_broad_query_can_surface_docs(tmp_path):
    write_graph(tmp_path)

    rows = graph_rows(tmp_path, "ams", limit=4)

    paths = [r["path"] for r in rows]
    assert "README.md" in paths
    assert "docs/ams-dashboard-plan.md" in paths


def test_artifact_nouns_imply_edit_only_without_concept_language():
    for query in (
        "test for checkout payment redirect",
        "tests for checkout payment redirect",
        "database migration for session expiry",
        "database migrations for session expiry",
    ):
        assert is_edit_intent(query_terms(query))

    for query in (
        "explain checkout test architecture",
        "checkout tests overview",
        "explain database migration architecture",
        "migration design docs",
    ):
        terms = query_terms(query)
        assert is_concept_intent(terms)
        assert not is_edit_intent(terms)


def test_explicit_edit_verbs_override_concept_language_for_artifacts():
    for query in (
        "fix checkout tests architecture",
        "debug checkout test overview",
        "implement migration design docs",
        "fix migrations architecture overview",
    ):
        terms = query_terms(query)
        assert is_edit_intent(terms)
        assert not is_concept_intent(terms)


def test_test_artifact_ranking_is_action_aware_and_explained(tmp_path):
    graph_dir = tmp_path / ".mimry" / "mimry-out" / "graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    label = "checkout tests payment redirect architecture overview"
    graph = {
        "nodes": [
            {
                "id": "checkout-payment-redirect-tests",
                "label": label,
                "source_file": "web/tests/checkout.test.tsx",
            },
            {
                "id": "checkout-tests-architecture-docs",
                "label": label,
                "source_file": "docs/checkout-tests-architecture.md",
            },
        ],
        "links": [],
    }
    (graph_dir / "graph.json").write_text(json.dumps(graph), encoding="utf-8")

    action_rows = graph_rows(tmp_path, "tests for checkout payment redirect", limit=2)
    assert [row["path"] for row in action_rows] == [
        "web/tests/checkout.test.tsx",
        "docs/checkout-tests-architecture.md",
    ]
    assert "test intent boost" in action_rows[0]["reason"]
    assert "doc downrank for edit intent" in action_rows[1]["reason"]

    concept_rows = graph_rows(tmp_path, "explain checkout tests architecture", limit=2)
    assert concept_rows[0]["path"] == "docs/checkout-tests-architecture.md"
    assert "concept doc boost" in concept_rows[0]["reason"]
    test_row = next(row for row in concept_rows if row["path"].startswith("web/tests/"))
    assert "test intent boost" not in test_row["reason"]
    assert "doc downrank for edit intent" not in concept_rows[0]["reason"]

    explicit_edit_rows = graph_rows(tmp_path, "fix checkout tests architecture overview", limit=2)
    assert explicit_edit_rows[0]["path"] == "web/tests/checkout.test.tsx"
    assert "test intent boost" in explicit_edit_rows[0]["reason"]
    assert "doc downrank for edit intent" in explicit_edit_rows[1]["reason"]


def test_migration_artifact_ranking_is_action_aware_and_explained(tmp_path):
    graph_dir = tmp_path / ".mimry" / "mimry-out" / "graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    label = "customer audit retention database migration architecture design docs"
    graph = {
        "nodes": [
            {
                "id": "customer-audit-retention-migration",
                "label": label,
                "source_file": "db/migrations/20260728_customer_audit_retention.sql",
            },
            {
                "id": "customer-audit-retention-migration-docs",
                "label": label,
                "source_file": "docs/database-migration-architecture.md",
            },
        ],
        "links": [],
    }
    (graph_dir / "graph.json").write_text(json.dumps(graph), encoding="utf-8")

    action_rows = graph_rows(tmp_path, "migration for customer audit retention", limit=2)
    assert [row["path"] for row in action_rows] == [
        "db/migrations/20260728_customer_audit_retention.sql",
        "docs/database-migration-architecture.md",
    ]
    assert "migration intent boost" in action_rows[0]["reason"]
    assert "doc downrank for edit intent" in action_rows[1]["reason"]

    concept_rows = graph_rows(tmp_path, "explain database migration architecture", limit=2)
    assert concept_rows[0]["path"] == "docs/database-migration-architecture.md"
    assert "concept doc boost" in concept_rows[0]["reason"]
    migration_row = next(row for row in concept_rows if row["path"].startswith("db/migrations/"))
    assert "migration intent boost" not in migration_row["reason"]
    assert "doc downrank for edit intent" not in concept_rows[0]["reason"]

    explicit_edit_rows = graph_rows(tmp_path, "implement migration architecture design docs", limit=2)
    assert explicit_edit_rows[0]["path"] == "db/migrations/20260728_customer_audit_retention.sql"
    assert "migration intent boost" in explicit_edit_rows[0]["reason"]
    assert "doc downrank for edit intent" in explicit_edit_rows[1]["reason"]


def test_graph_edit_intent_promotes_source_above_docs_and_explains(tmp_path):
    write_graph(tmp_path)

    rows = graph_rows(tmp_path, "fix ams monitor dashboard implementation", limit=4)

    paths = [r["path"] for r in rows]
    assert paths.index("src/ams/monitor_dashboard.py") < paths.index("README.md")
    assert paths.index("src/ams/monitor_dashboard.py") < paths.index("docs/ams-dashboard-plan.md")
    source_reason = next(r["reason"] for r in rows if r["path"] == "src/ams/monitor_dashboard.py")
    doc_reason = next(r["reason"] for r in rows if r["path"] == "README.md")
    assert "intent source boost" in source_reason
    assert "doc downrank for edit intent" in doc_reason


def test_graph_rows_consolidate_file_topology_reasons_without_changing_score(tmp_path):
    graph_dir = tmp_path / ".mimry" / "mimry-out" / "graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
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

    rows = graph_rows(tmp_path, "widget")
    by_path = {row["path"]: row for row in rows}
    row = by_path["src/widgets.py"]

    # Files reached along relationships now appear as additional lower-scored rows.
    # helper-a and helper-b never match "widget" textually; they are here only
    # because the matched nodes point at them, which is the whole point of a graph.
    assert set(by_path) == {"src/widgets.py", "src/a.py", "src/b.py"}
    assert by_path["src/a.py"]["score"] < row["score"]
    assert by_path["src/b.py"]["score"] < row["score"]
    assert "reached by MIMRY graph relationship" in by_path["src/a.py"]["reason"]

    # The directly matched file keeps its exact score: expansion must not disturb it.
    # (45 label + 35 source + 5 degree + 5 community)
    # + (45 label + 35 source + 10 degree + 5 community).
    assert row["score"] == 185
    assert "graph topology boost 25 across 2 matched nodes" in row["reason"]
    assert "max degree 2" in row["reason"]
    assert "2 communities" in row["reason"]
    assert "graph degree 1" not in row["reason"]
    assert "graph degree 2" not in row["reason"]
