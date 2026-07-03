from __future__ import annotations

from mimry.adapters import adapter_for_extension, list_adapters
from mimry.framework_adapters import markdown_doc_facts, nextjs_app_router_facts


def test_adapter_registry_lists_active_and_planned_adapters():
    adapters = list_adapters()
    names = {a["name"] for a in adapters}
    assert "python-ast" in names
    assert "js-ts-regex" in names
    assert "typescript-ast" in names
    assert "fastapi" in names
    assert "nextjs-app-router" in names
    assert "react-native-expo" in names
    assert "sql-schema" in names
    assert "markdown-docs" in names
    assert any(a["status"] == "planned" for a in adapters)


def test_active_only_adapter_registry_hides_planned_adapters():
    adapters = list_adapters(include_planned=False)
    assert {a["status"] for a in adapters} == {"active"}
    assert "python-ast" in {a["name"] for a in adapters}
    assert "typescript-ast" in {a["name"] for a in adapters}
    assert "config-manifest" in {a["name"] for a in adapters}
    assert "nextjs-app-router" in {a["name"] for a in adapters}
    assert "fastapi" in {a["name"] for a in adapters}
    assert "react-native-expo" in {a["name"] for a in adapters}
    assert "sql-schema" in {a["name"] for a in adapters}
    assert "markdown-docs" in {a["name"] for a in adapters}
    assert "js-ts-regex" not in {a["name"] for a in adapters}


def test_adapter_for_extension_routes_known_code_extensions():
    assert adapter_for_extension(".py").name == "python-ast"
    assert adapter_for_extension(".tsx").name == "typescript-ast"
    assert adapter_for_extension(".unknown").name == "generic-text"


def test_nextjs_app_router_extracts_route_facts():
    facts = nextjs_app_router_facts("src/app/board/[cardId]/page.tsx")

    assert "nextjs app router route /board/:cardId kind page file src/app/board/[cardId]/page.tsx" in facts
    assert any("dynamic route segments [cardId]" in fact for fact in facts)


def test_markdown_docs_extracts_frontmatter_headings_and_wiki_links(tmp_path):
    doc = tmp_path / "decision.md"
    doc.write_text(
        "---\ntitle: Board Routes\ntype: adr\ntags: [next, fastapi]\n---\n# Route Map\nSee [[Backend API]] and [Schema](schema.md).\n",
        encoding="utf-8",
    )

    facts = " | ".join(markdown_doc_facts(doc, tmp_path))

    assert "markdown frontmatter" in facts
    assert "title Board Routes" in facts
    assert "markdown headings Route Map" in facts
    assert "markdown wiki links Backend API" in facts
    assert "markdown doc links schema.md" in facts
