from __future__ import annotations

import json
from pathlib import Path

from mimry.core.artifacts import graph_rows
from mimry.intent import (
    excluded_query_terms,
    is_concept_intent,
    is_edit_intent,
    is_source_file,
    is_test_file,
    query_terms,
    term_matches_token,
)
from mimry.search import find_rows
from mimry.storage import write_jsonl


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


def test_graph_rows_score_a_file_by_its_best_match_and_expansion_leaves_it_alone(tmp_path):
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
    # "widget" is in 1 of 3 files, weight w = 1 + ln(4/2). Best node "first":
    # 30w label + 25w path + 5 for a second matching node + 10 topology
    # (degree 1, community) = 108, then +100 intent source boost.
    assert row["score"] == 208
    assert "graph topology boost 10 (degree 1, in a community)" in row["reason"]


def test_negative_scope_terms_do_not_boost_excluded_graph_paths_and_are_explained(tmp_path):
    graph_dir = tmp_path / ".mimry" / "mimry-out" / "graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    shared = "marketing hero copy"
    graph = {
        "nodes": [
            {"id": "frontend-hero", "label": shared, "source_file": "frontend/src/marketing/hero-section.tsx"},
            {"id": "backend-hero", "label": shared, "source_file": "backend/services/marketing_hero.py"},
        ],
        "links": [],
    }
    (graph_dir / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    idx = tmp_path / "index"
    idx.mkdir()
    write_jsonl(
        idx / "files.jsonl",
        [
            {
                "file_id": node["id"],
                "filename": Path(node["source_file"]).name,
                "rel_path": node["source_file"],
                "content_hint": shared,
                "metadata_text": "",
                "adapter": "python-ast",
            }
            for node in graph["nodes"]
        ],
    )

    queries = (
        "marketing hero copy without touching backend",
        "frontend only, exclude backend",
        'marketing hero copy; exclude "backend services"',
    )
    for query in queries:
        assert "backend" in excluded_query_terms(query)
        assert "backend" not in query_terms(query)
        rows = find_rows(idx, query, limit=2, graph=True, root=tmp_path)
        assert rows[0]["path"] == "frontend/src/marketing/hero-section.tsx"
        backend_rows = [row for row in rows if row["path"].startswith("backend/")]
        if backend_rows:
            assert backend_rows[0]["score"] < rows[0]["score"]
            assert "excluded scope downrank: backend" in backend_rows[0]["reason"]
        else:
            assert query == "frontend only, exclude backend"

    assert excluded_query_terms("improve hero without touching backend while editing frontend") == ["backend"]
    assert excluded_query_terms("backend marketing hero copy") == []
    assert "backend" in query_terms("backend marketing hero copy")
    positive_rows = find_rows(idx, "backend marketing hero copy", limit=2, graph=True, root=tmp_path)
    assert positive_rows[0]["path"] == "backend/services/marketing_hero.py"
    assert "excluded scope downrank" not in positive_rows[0]["reason"]


def test_positive_except_and_negated_exclude_constructions_keep_backend_positive(tmp_path):
    graph_dir = tmp_path / ".mimry" / "mimry-out" / "graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    graph = {
        "nodes": [
            {
                "id": "backend-hero",
                "label": "backend marketing hero copy",
                "source_file": "backend/services/marketing_hero.py",
            }
        ],
        "links": [],
    }
    (graph_dir / "graph.json").write_text(json.dumps(graph), encoding="utf-8")

    for query in (
        "nothing except backend",
        "do not exclude backend",
        "do not  exclude backend",
        "don't exclude backend",
    ):
        assert excluded_query_terms(query) == []
        assert "backend" in query_terms(query)
        rows = graph_rows(tmp_path, query, limit=10)
        assert rows
        assert any("backend" in row["path"] for row in rows)
        assert all("excluded scope downrank" not in row["reason"] for row in rows)


def test_bounded_conjoined_negative_scopes_exclude_each_simple_term_and_stop_at_positive_clause():
    assert excluded_query_terms("exclude backend and infrastructure") == ["backend", "infrastructure"]
    assert query_terms("exclude backend and infrastructure") == []
    assert excluded_query_terms("without touching backend and tests") == ["backend", "tests"]
    assert "backend" not in query_terms("without touching backend and tests")
    assert "tests" not in query_terms("without touching backend and tests")

    query = "exclude backend and infrastructure while editing frontend"
    assert excluded_query_terms(query) == ["backend", "infrastructure"]
    assert "frontend" in query_terms(query)

    assert excluded_query_terms("exclude: backend") == ["backend"]


def test_merged_graph_and_content_score_gets_one_final_exclusion_adjustment(tmp_path):
    graph_dir = tmp_path / ".mimry" / "mimry-out" / "graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    path = "backend/services/marketing_hero.py"
    graph = {
        "nodes": [{"id": "backend-hero", "label": "marketing hero copy", "source_file": path}],
        "links": [],
    }
    (graph_dir / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    idx = tmp_path / "index"
    idx.mkdir()
    write_jsonl(
        idx / "files.jsonl",
        [
            {
                "file_id": "backend-hero",
                "filename": "marketing_hero.py",
                "rel_path": path,
                "content_hint": "marketing hero copy",
                "metadata_text": "",
                "adapter": "python-ast",
            }
        ],
    )

    positive = find_rows(idx, "marketing hero copy", limit=10, graph=True, root=tmp_path)
    negative = find_rows(idx, "marketing hero copy without touching backend", limit=10, graph=True, root=tmp_path)

    assert positive[0]["path"] == negative[0]["path"] == path
    assert negative[0]["score"] == max(1, int(positive[0]["score"] * 0.1))
    assert negative[0]["reason"].count("excluded scope downrank: backend") == 1


def _graph(root: Path, nodes: list[dict], links: list[dict] | None = None) -> None:
    graph_dir = root / ".mimry" / "mimry-out" / "graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / "graph.json").write_text(json.dumps({"nodes": nodes, "links": links or []}), encoding="utf-8")


def _node(node_id: str, label: str, source_file: str) -> dict:
    return {"id": node_id, "label": label, "source_file": source_file}


def test_a_test_file_with_many_matching_cases_does_not_outrank_the_source(tmp_path):
    cases = [_node(f"t{i}", f"test fish completion case {i}", "tests/test_fish.py") for i in range(12)]
    _graph(tmp_path, [_node("src", "fish completion", "src/fish.py"), *cases])

    rows = graph_rows(tmp_path, "fish completion", limit=2)

    assert [row["path"] for row in rows] == ["src/fish.py", "tests/test_fish.py"]


def test_a_path_match_counts_once_per_file_not_once_per_node(tmp_path):
    many = [_node(f"m{i}", f"helper {i}", "src/widget.py") for i in range(10)]
    _graph(tmp_path, [*many, _node("one", "widget", "src/other.py")])

    rows = {row["path"]: row["score"] for row in graph_rows(tmp_path, "widget")}

    # Ten nodes in widget.py do not multiply its single path match.
    assert rows["src/widget.py"] <= rows["src/other.py"] * 1.5


def test_a_rare_query_word_outweighs_more_common_ones(tmp_path):
    common = [_node(f"c{i}", "shell completion script", f"src/{name}.py") for i, name in enumerate("abcdefgh")]
    _graph(tmp_path, [*common, _node("fish", "fish completion", "src/fish.py")])

    # Every file matches "completion"; only one matches "fish".
    rows = graph_rows(tmp_path, "fish shell completion script", limit=1)

    assert rows[0]["path"] == "src/fish.py"


def test_a_hub_file_reached_from_many_matches_gets_one_contribution(tmp_path):
    matched = [_node(f"m{i}", "session renew", f"src/session_{i}.py") for i in range(6)]
    helpers = [_node(f"h{i}", f"helper {i}", "src/utils.py") for i in range(6)]
    links = [{"source": f"m{i}", "target": f"h{i}"} for i in range(6)]
    _graph(tmp_path, [*matched, *helpers], links)

    rows = graph_rows(tmp_path, "session renew", limit=10)
    by_path = {row["path"]: row["score"] for row in rows}

    assert by_path["src/utils.py"] < min(score for path, score in by_path.items() if path != "src/utils.py")


def test_query_words_match_whole_tokens_with_short_inflections():
    assert term_matches_token("completion", "completions")
    assert term_matches_token("completions", "completion")
    assert not term_matches_token("art", "start")
    assert not term_matches_token("args", "argsparser")
    for term, token in (("token", "tokens"), ("parse", "parser"), ("render", "rendered"), ("cache", "caching")):
        assert term_matches_token(term, token), (term, token)
    # A shared prefix is not an inflection.
    for term, token in (("auth", "author"), ("read", "ready"), ("sign", "signal"), ("time", "timeout")):
        assert not term_matches_token(term, token), (term, token)


def test_requested_action_words_do_not_locate_code(tmp_path):
    _graph(tmp_path, [_node("fixture", "fix fixtures", "tests/fixtures.py"), _node("w", "widget", "src/widget.py")])

    rows = graph_rows(tmp_path, "fix widget", limit=10)

    assert [row["path"] for row in rows] == ["src/widget.py"]


def test_test_and_source_files_are_classified_across_languages():
    for path in (
        "command_test.go",
        "src/test/java/com/acme/WidgetTest.java",
        "Acme.Tests/WidgetTests.cs",
        "packages/zod/src/v4/classic/tests/error.test.ts",
        "crates/core/tests/integration.rs",
        "spec/widget_spec.rb",
        "src/test_widget.ts",
        "polls/tests.py",
    ):
        assert is_test_file(path), path
        assert not is_source_file(path), path
    for path in ("command.go", "crates/ignore/src/walk.rs", "httpx/_client.py", "src/flask/app.py", "Contest.java"):
        assert is_source_file(path), path
    for path in ("examples/tutorial/app.py", "vendor/lib/x.go", "docs/conf.py", "README.md"):
        assert not is_source_file(path), path


def test_a_task_title_is_an_edit_unless_it_asks_to_explain():
    for title in ("Deprecate the old flag", "Add support for fish shell", "Prevent duplicate headers"):
        assert is_edit_intent(query_terms(title)), title
    assert not is_edit_intent(query_terms("explain the session design"))


def test_english_function_words_in_a_task_title_do_not_locate_code(tmp_path):
    _graph(tmp_path, [_node("noise", "when their", "src/when.py"), _node("s", "session renew login", "src/session.py")])

    rows = graph_rows(tmp_path, "users being logged out when their login is renewed", limit=10)

    assert [row["path"] for row in rows] == ["src/session.py"]
