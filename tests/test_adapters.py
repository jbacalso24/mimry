from __future__ import annotations

from mimry.adapters import adapter_for_extension, list_adapters
from mimry.framework_adapters import markdown_doc_facts, nextjs_app_router_facts


def test_adapter_registry_lists_all_active_adapters():
    adapters = list_adapters(include_planned=False)
    names = {a["name"] for a in adapters}
    assert "python-ast" in names
    assert "js-ts-regex" in names
    assert "typescript-ast" in names
    assert "fastapi" in names
    assert "nextjs-app-router" in names
    assert "react-native-expo" in names
    assert "sql-schema" in names
    assert "sql-alembic" in names
    assert "swift-ios" in names
    assert "markdown-docs" in names
    assert "office-document" in names
    assert "pdf-document" in names
    assert "svg-document" in names


def test_active_only_adapter_registry_has_all_active():
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
    assert "office-document" in {a["name"] for a in adapters}
    assert "pdf-document" in {a["name"] for a in adapters}
    assert "svg-document" in {a["name"] for a in adapters}
    assert "js-ts-regex" in {a["name"] for a in adapters}
    assert "sql-alembic" in {a["name"] for a in adapters}
    assert "swift-ios" in {a["name"] for a in adapters}


def test_adapter_for_extension_routes_known_code_extensions():
    assert adapter_for_extension(".py").name == "python-ast"
    assert adapter_for_extension(".tsx").name == "typescript-ast"
    assert adapter_for_extension(".unknown").name == "generic-text"


def test_nextjs_app_router_extracts_route_facts():
    facts = nextjs_app_router_facts("src/app/board/[cardId]/page.tsx")

    assert (
        "nextjs app router route /board/:cardId kind page file src/app/board/[cardId]/page.tsx"
        in facts
    )
    assert any("dynamic route segments [cardId]" in fact for fact in facts)


def test_markdown_docs_extracts_frontmatter_headings_and_wiki_links(tmp_path):
    doc = tmp_path / "decision.md"
    doc.write_text(
        "---\ntitle: Board Routes\ntype: adr\ntags: [next, fastapi]\n---\n# Route Map\nSee"
        " [[Backend API]] and [Schema](schema.md).\n",
        encoding="utf-8",
    )

    facts = " | ".join(markdown_doc_facts(doc, tmp_path))

    assert "markdown frontmatter" in facts
    assert "title Board Routes" in facts
    assert "markdown headings Route Map" in facts
    assert "markdown wiki links Backend API" in facts
    assert "markdown doc links schema.md" in facts


def test_alembic_migration_facts_detects_migration(tmp_path):
    from mimry.framework_adapters import alembic_migration_facts

    migration = tmp_path / "migration.py"
    migration.write_text(
        "from alembic import op\nrevision = 'abc123'\ndown_revision = 'xyz789'\n"
        "branch_labels = None\ndepends_on = None\n",
        encoding="utf-8",
    )

    facts = alembic_migration_facts(migration.read_text(), migration, tmp_path, {})

    assert any("abc123" in f for f in facts)
    assert any("xyz789" in f for f in facts)


def test_alembic_migration_without_marker_returns_empty(tmp_path):
    from mimry.framework_adapters import alembic_migration_facts

    non_migration = tmp_path / "regular.py"
    non_migration.write_text("revision = 'test'\nx = 1\n", encoding="utf-8")

    facts = alembic_migration_facts(non_migration.read_text(), non_migration, tmp_path, {})

    assert not facts


def test_plist_facts_extraction(tmp_path):
    from mimry.framework_adapters import plist_facts

    info_plist = tmp_path / "Info.plist"
    plist_content = (
        b"<?xml version='1.0' encoding='UTF-8'?>"
        b"<!DOCTYPE plist PUBLIC '-//Apple//DTD PLIST 1.0//EN'"
        b" 'http://www.apple.com/DTDs/PropertyList-1.0.dtd'>"
        b"<plist version='1.0'>"
        b"<dict>"
        b"<key>CFBundleIdentifier</key><string>com.example.app</string>"
        b"</dict>"
        b"</plist>"
    )
    info_plist.write_bytes(plist_content)

    facts = plist_facts(info_plist, tmp_path, {}, data=plist_content)

    assert any("com.example.app" in f for f in facts)


def test_entitlements_facts_extraction(tmp_path):
    from mimry.framework_adapters import plist_facts

    entitlements = tmp_path / "app.entitlements"
    plist_content = (
        b"<?xml version='1.0' encoding='UTF-8'?>"
        b"<!DOCTYPE plist PUBLIC '-//Apple//DTD PLIST 1.0//EN'"
        b" 'http://www.apple.com/DTDs/PropertyList-1.0.dtd'>"
        b"<plist version='1.0'>"
        b"<dict>"
        b"<key>com.apple.security.application-groups</key>"
        b"<array>"
        b"<string>group.com.example</string>"
        b"</array>"
        b"</dict>"
        b"</plist>"
    )
    entitlements.write_bytes(plist_content)

    facts = plist_facts(entitlements, tmp_path, {}, data=plist_content)

    assert any("group.com.example" in f for f in facts)


def test_js_ts_regex_fallback_recovers_declarations():
    import textwrap

    from mimry.ts_ast_adapter import _fallback_symbols_regex

    source = textwrap.dedent(
        """
        export function myFunc() {}
        class MyClass {}
        interface MyInterface {}
        type MyType = string;
        enum MyEnum {}
        const MyComponent = () => <div/>
        """
    ).strip()
    file_record = {"file_id": "test_id"}

    from pathlib import Path

    symbols, edges = _fallback_symbols_regex(source, file_record, Path("test.tsx"))

    names = {s["name"] for s in symbols}
    # Fallback recovers functions, classes, and arrow functions (as
    # variables), not interfaces, types, or enums
    assert "myFunc" in names
    assert "MyClass" in names
    assert "MyComponent" in names
    assert "MyInterface" not in names
    assert "MyType" not in names
    assert "MyEnum" not in names
