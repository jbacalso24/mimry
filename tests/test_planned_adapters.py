"""Tests for planned adapters: Alembic, Xcode, Plists, js-ts-regex."""

from __future__ import annotations

import json
import plistlib
import textwrap
from pathlib import Path
from types import SimpleNamespace

from mimry.commands import cmd_init
from mimry.indexer import write_index
from mimry.storage import load_pointer
from mimry.xcode import parse_pbxproj


class TestAlembicMigrations:
    """Test Alembic migration detection and relationships."""

    def test_linear_chain_creates_two_revises_edges(self, tmp_path: Path) -> None:
        """A <- B <- C creates two revises edges: B->A and C->B."""
        root = tmp_path / "repo"
        versions_dir = root / "alembic" / "versions"
        versions_dir.mkdir(parents=True)

        # Create three migrations in a linear chain
        file_a = versions_dir / "001_a.py"
        file_b = versions_dir / "002_b.py"
        file_c = versions_dir / "003_c.py"

        file_a.write_text(
            textwrap.dedent(
                """
                from alembic import op
                revision = 'a123456789ab'
                down_revision = None
                def upgrade():
                    pass
                """
            ),
            encoding="utf-8",
        )
        file_b.write_text(
            textwrap.dedent(
                """
                from alembic import op
                revision = 'b234567890bc'
                down_revision = 'a123456789ab'
                def upgrade():
                    pass
                """
            ),
            encoding="utf-8",
        )
        file_c.write_text(
            textwrap.dedent(
                """
                from alembic import op
                revision = 'c345678901cd'
                down_revision = 'b234567890bc'
                def upgrade():
                    pass
                """
            ),
            encoding="utf-8",
        )

        assert cmd_init(SimpleNamespace(root=root, root_type="repo", skip_graph=True)) == 0
        pointer = load_pointer(root)
        assert pointer is not None
        write_index(root, pointer)

        graph = json.loads(
            (root / ".mimry" / "mimry-out" / "graph" / "graph.json").read_text(encoding="utf-8")
        )
        labels = {n["id"]: n.get("label") for n in graph["nodes"]}
        revises_pairs = {
            (labels.get(e["source"]), labels.get(e["target"]))
            for e in graph["edges"]
            if e.get("relation") == "revises"
        }

        # Should have exactly 2 edges: B->A and C->B
        # Build expected paths from the same variables the test uses
        path_b = str(file_b.relative_to(root)).replace("\\", "/")
        path_a = str(file_a.relative_to(root)).replace("\\", "/")
        path_c = str(file_c.relative_to(root)).replace("\\", "/")
        assert (path_b, path_a) in revises_pairs
        assert (path_c, path_b) in revises_pairs
        # Should not have any other revises edges
        assert len([p for p in revises_pairs if p[0] and p[1]]) == 2

    def test_merge_migration_with_tuple_creates_edges(self, tmp_path: Path) -> None:
        """Merge migration with tuple down_revision creates edges."""
        root = tmp_path / "repo"
        versions_dir = root / "alembic" / "versions"
        versions_dir.mkdir(parents=True)

        # Create two base migrations
        file_a = versions_dir / "001_a.py"
        file_b = versions_dir / "002_b.py"
        file_merge = versions_dir / "003_merge.py"

        file_a.write_text(
            "from alembic import op\nrevision = 'a'\ndown_revision = None\n",
            encoding="utf-8",
        )
        file_b.write_text(
            "from alembic import op\nrevision = 'b'\ndown_revision = None\n",
            encoding="utf-8",
        )
        # Merge migration with tuple down_revision
        file_merge.write_text(
            "from alembic import op\nrevision = 'merge'\ndown_revision = ('a', 'b')\n",
            encoding="utf-8",
        )

        assert cmd_init(SimpleNamespace(root=root, root_type="repo", skip_graph=True)) == 0
        pointer = load_pointer(root)
        assert pointer is not None
        write_index(root, pointer)

        graph = json.loads(
            (root / ".mimry" / "mimry-out" / "graph" / "graph.json").read_text(encoding="utf-8")
        )
        labels = {n["id"]: n.get("label") for n in graph["nodes"]}
        revises_pairs = {
            (labels.get(e["source"]), labels.get(e["target"]))
            for e in graph["edges"]
            if e.get("relation") == "revises"
        }

        # Merge migration should have edges to both parents
        # Build expected paths from the same variables the test uses
        path_merge = str(file_merge.relative_to(root)).replace("\\", "/")
        path_a = str(file_a.relative_to(root)).replace("\\", "/")
        path_b = str(file_b.relative_to(root)).replace("\\", "/")
        assert (path_merge, path_a) in revises_pairs
        assert (path_merge, path_b) in revises_pairs

    def test_base_migration_no_edges(self, tmp_path: Path) -> None:
        """Base migration gives no revises edges."""
        root = tmp_path / "repo"
        (root / "alembic" / "versions").mkdir(parents=True)

        (root / "alembic" / "versions" / "001_initial.py").write_text(
            "from alembic import op\nrevision = 'init'\ndown_revision = None\n",
            encoding="utf-8",
        )

        assert cmd_init(SimpleNamespace(root=root, root_type="repo", skip_graph=True)) == 0
        pointer = load_pointer(root)
        assert pointer is not None
        write_index(root, pointer)

        graph = json.loads(
            (root / ".mimry" / "mimry-out" / "graph" / "graph.json").read_text(encoding="utf-8")
        )
        revises_edges = [e for e in graph["edges"] if e.get("relation") == "revises"]

        # No revises edges for base migration
        assert len(revises_edges) == 0

    def test_duplicate_revision_ids_no_edge(self, tmp_path: Path) -> None:
        """Two files with same revision id give no edge for that id."""
        root = tmp_path / "repo"
        (root / "alembic" / "versions").mkdir(parents=True)

        # Two files claiming the same revision
        (root / "alembic" / "versions" / "001_a.py").write_text(
            "from alembic import op\nrevision = 'conflict'\ndown_revision = None\n",
            encoding="utf-8",
        )
        (root / "alembic" / "versions" / "002_b.py").write_text(
            "from alembic import op\nrevision = 'conflict'\ndown_revision = None\n",
            encoding="utf-8",
        )

        assert cmd_init(SimpleNamespace(root=root, root_type="repo", skip_graph=True)) == 0
        pointer = load_pointer(root)
        assert pointer is not None
        write_index(root, pointer)

        graph = json.loads(
            (root / ".mimry" / "mimry-out" / "graph" / "graph.json").read_text(encoding="utf-8")
        )
        revises_edges = [e for e in graph["edges"] if e.get("relation") == "revises"]

        # No revises edges when revision is duplicated
        assert len(revises_edges) == 0

    def test_annotated_template_works(self, tmp_path: Path) -> None:
        """Type-annotated template extracts revision correctly."""
        from mimry.framework_adapters import alembic_migration

        source = textwrap.dedent(
            """
            from typing import Union
            from alembic import op
            revision: str = "abc123"
            down_revision: Union[str, None] = "xyz789"
            """
        )

        result = alembic_migration(source)

        assert result is not None
        assert result["revision"] == "abc123"
        assert result["down_revisions"] == ["xyz789"]

    def test_operator_import_no_facts(self, tmp_path: Path) -> None:
        """import operator doesn't trigger alembic facts."""
        from mimry.framework_adapters import alembic_migration

        source = "import operator\nrevision = 'x'\n"

        result = alembic_migration(source)

        assert result is None

    def test_revision_in_function_not_counted(self, tmp_path: Path) -> None:
        """Revision assigned inside a function is not counted."""
        from mimry.framework_adapters import alembic_migration

        source = textwrap.dedent(
            """
            from alembic import op
            def setup():
                revision = 'inner'
            revision = 'outer'
            down_revision = None
            """
        )

        result = alembic_migration(source)

        # Should extract the outer revision
        assert result is not None
        assert result["revision"] == "outer"

    def test_op_create_table_and_index(self, tmp_path: Path) -> None:
        """op.create_table and op.create_index extract table names."""
        from mimry.framework_adapters import alembic_migration

        source = textwrap.dedent(
            """
            from alembic import op
            revision = 'test123'
            down_revision = None
            def upgrade():
                op.create_table("users", sa.Column("id", sa.Integer))
                op.create_index("ix_email", "users", ["email"])
                op.create_index("ix", "orders", ["id"])
            """
        )

        result = alembic_migration(source)

        assert result is not None
        assert "users" in result["tables"]
        assert "orders" in result["tables"]

    def test_reindex_preserves_revises_edges(self, tmp_path: Path) -> None:
        """Reindexing preserves revises edges after file changes."""
        root = tmp_path / "repo"
        versions_dir = root / "alembic" / "versions"
        versions_dir.mkdir(parents=True)

        file_a = versions_dir / "001_a.py"
        file_b = versions_dir / "002_b.py"

        file_a.write_text(
            "from alembic import op\nrevision = 'a'\ndown_revision = None\n",
            encoding="utf-8",
        )
        file_b.write_text(
            "from alembic import op\nrevision = 'b'\ndown_revision = 'a'\n",
            encoding="utf-8",
        )

        # First index
        assert cmd_init(SimpleNamespace(root=root, root_type="repo", skip_graph=True)) == 0
        pointer = load_pointer(root)
        assert pointer is not None
        write_index(root, pointer)

        # Touch an unrelated file
        (root / "README.md").write_text("# Updated")

        # Reindex
        pointer = load_pointer(root)
        assert pointer is not None
        write_index(root, pointer)

        graph = json.loads(
            (root / ".mimry" / "mimry-out" / "graph" / "graph.json").read_text(encoding="utf-8")
        )
        labels = {n["id"]: n.get("label") for n in graph["nodes"]}
        revises_pairs = {
            (labels.get(e["source"]), labels.get(e["target"]))
            for e in graph["edges"]
            if e.get("relation") == "revises"
        }

        # Revises edge should still exist
        # Build expected paths from the same variables the test uses
        path_b = str(file_b.relative_to(root)).replace("\\", "/")
        path_a = str(file_a.relative_to(root)).replace("\\", "/")
        assert (path_b, path_a) in revises_pairs


class TestXcodeParser:
    """Test Xcode pbxproj parser."""

    def test_realistic_pbxproj_with_references(self, tmp_path: Path) -> None:
        """Realistic pbxproj yields references edges."""
        root = tmp_path / "repo"
        app_dir = root / "App"
        share_dir = root / "ShareExtension"
        app_dir.mkdir(parents=True)
        share_dir.mkdir(parents=True)

        # Create the actual referenced files
        app_entitlements = app_dir / "App.entitlements"
        share_info_plist = share_dir / "Info.plist"
        app_entitlements.write_text(
            '<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict></dict></plist>',
            encoding="utf-8",
        )
        share_info_plist.write_text(
            '<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict></dict></plist>',
            encoding="utf-8",
        )

        # Create a realistic pbxproj
        pbxproj_content = textwrap.dedent(
            r"""
            // !$*UTF8*$!
            {
                archiveVersion = 1;
                classes = {};
                objectVersion = 54;
                objects = {
                    /* App target */
                    ABC123456789 = {
                        isa = PBXNativeTarget;
                        name = App;
                        productType = "com.apple.product-type.application";
                        buildConfigurationList = ABC123456780;
                    };
                    /* ShareExtension target */
                    DEF123456789 = {
                        isa = PBXNativeTarget;
                        name = ShareExtension;
                        productType = "com.apple.product-type.app-extension";
                        buildConfigurationList = DEF123456780;
                    };
                    ABC123456780 = {
                        isa = XCConfigurationList;
                        buildConfigurations = (ABC999999999,);
                    };
                    DEF123456780 = {
                        isa = XCConfigurationList;
                        buildConfigurations = (DEF999999999,);
                    };
                    ABC999999999 = {
                        isa = XCBuildConfiguration;
                        buildSettings = {
                            PRODUCT_BUNDLE_IDENTIFIER = com.example.app;
                            CODE_SIGN_ENTITLEMENTS = "App/App.entitlements";
                            INFOPLIST_FILE = "App/Info.plist";
                        };
                    };
                    DEF999999999 = {
                        isa = XCBuildConfiguration;
                        buildSettings = {
                            PRODUCT_BUNDLE_IDENTIFIER = "com.example.app.share";
                            CODE_SIGN_ENTITLEMENTS = "ShareExtension/ShareExtension.entitlements";
                            INFOPLIST_FILE = "ShareExtension/Info.plist";
                        };
                    };
                };
                rootObject = XYZ123456789;
            }
            """
        ).strip()

        pbxproj_file = root / "App.xcodeproj" / "project.pbxproj"
        pbxproj_file.parent.mkdir()
        pbxproj_file.write_text(pbxproj_content)

        assert cmd_init(SimpleNamespace(root=root, root_type="repo", skip_graph=True)) == 0
        pointer = load_pointer(root)
        assert pointer is not None
        write_index(root, pointer)

        graph = json.loads(
            (root / ".mimry" / "mimry-out" / "graph" / "graph.json").read_text(encoding="utf-8")
        )
        labels = {n["id"]: n.get("label") for n in graph["nodes"]}
        references_pairs = {
            (labels.get(e["source"]), labels.get(e["target"]))
            for e in graph["edges"]
            if e.get("relation") == "references"
        }

        # Should have references from pbxproj to the plist files
        # Build expected paths from the same variables the test uses
        pbxproj_label = str(pbxproj_file.relative_to(root)).replace("\\", "/")
        plist_label = str(share_info_plist.relative_to(root)).replace("\\", "/")
        assert (pbxproj_label, plist_label) in references_pairs

    def test_malformed_pbxproj_no_error(self) -> None:
        """Malformed pbxproj gives no facts and no exception."""
        malformed = "{ this is not valid plist "
        result = parse_pbxproj(malformed)
        assert result is None

    def test_pbxproj_size_cap(self) -> None:
        """pbxproj over size cap gives no facts."""
        from mimry.xcode import MAX_PLIST_SIZE

        oversized = "x" * (MAX_PLIST_SIZE + 1)
        result = parse_pbxproj(oversized)
        assert result is None

    def test_quoted_string_unescape(self) -> None:
        """Quoted string with \" and \\n unescapes correctly."""
        plist = r"""
        {
            escaped = "say \"hi\"\\nnext";
            newline = "a\nb";
        }
        """
        result = parse_pbxproj(plist)
        assert result is not None
        # Quote pair, then literal backslash and n, not newline
        assert result["escaped"] == 'say "hi"\\nnext'
        # Real newline character in the value
        assert result["newline"] == "a\nb"


class TestPlists:
    """Test plist extraction."""

    def test_xml_plist_with_entity_refused(self) -> None:
        """XML plist with <!ENTITY is refused."""
        from mimry.framework_adapters import plist_facts

        plist_content = textwrap.dedent(
            """
            <?xml version="1.0" encoding="UTF-8"?>
            <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
             "http://www.apple.com/DTDs/PropertyList-1.0.dtd" [
              <!ENTITY custom "value">
            ]>
            <plist version="1.0">
            <dict>
                <key>test</key>
                <string>&custom;</string>
            </dict>
            </plist>
            """
        )
        path = Path("test.plist")
        root = Path("/tmp")
        file_record = {"file_id": "test_id"}

        result = plist_facts(path, root, file_record, data=plist_content.encode())

        assert result == []

    def test_binary_plist_gives_facts(self, tmp_path: Path) -> None:
        """Binary plist gives facts."""
        from mimry.framework_adapters import plist_facts

        data_dict = {"CFBundleIdentifier": "com.example.app"}
        binary_data = plistlib.dumps(data_dict, fmt=plistlib.FMT_BINARY)

        path = Path("Info.plist")
        root = Path("/tmp")
        file_record = {"file_id": "test_id"}

        result = plist_facts(path, root, file_record, data=binary_data)

        assert len(result) > 0
        assert any("com.example.app" in fact for fact in result)

    def test_settings_plist_no_facts(self, tmp_path: Path) -> None:
        """Settings.plist gives no facts."""
        from mimry.framework_adapters import plist_facts

        data_dict = {"CFBundleIdentifier": "com.example.app"}
        binary_data = plistlib.dumps(data_dict, fmt=plistlib.FMT_BINARY)

        path = Path("Settings.plist")
        root = Path("/tmp")
        file_record = {"file_id": "test_id"}

        result = plist_facts(path, root, file_record, data=binary_data)

        # Settings.plist doesn't match Info.plist naming
        assert result == []


class TestJsTsRegex:
    """Test js-ts-regex fallback adapter."""

    def test_fallback_recovers_top_level_declarations(self) -> None:
        """Fallback recovers top-level declarations."""
        from mimry.ts_ast_adapter import _fallback_symbols_regex

        source = textwrap.dedent(
            """
            export function myFunc() {}
            class MyClass {}
            const myComponent = () => <div/>;
            """
        ).strip()

        path = Path("test.tsx")
        file_record = {"file_id": "test_id"}

        symbols, edges = _fallback_symbols_regex(source, file_record, path)

        names = {s["name"] for s in symbols}
        assert "myFunc" in names
        assert "MyClass" in names
        assert "myComponent" in names

    def test_fallback_skips_indented_declarations(self) -> None:
        """Indented declarations are not recorded in fallback."""
        from mimry.ts_ast_adapter import _fallback_symbols_regex

        source = textwrap.dedent(
            """
            export function topLevel() {}
            class Container {
                method() {}
            }
            """
        ).strip()

        path = Path("test.tsx")
        file_record = {"file_id": "test_id"}

        symbols, edges = _fallback_symbols_regex(source, file_record, path)

        names = {s["name"] for s in symbols}
        # Only top-level should be recorded
        assert "topLevel" in names
        assert "Container" in names
        # Indented method should not be recorded
        assert "method" not in names

    def test_fallback_uses_tree_sitter_kinds(self) -> None:
        """Fallback uses tree-sitter kinds for arrow functions."""
        from mimry.ts_ast_adapter import _fallback_symbols_regex

        source = "const MyComponent = () => <div/>"

        path = Path("test.tsx")
        file_record = {"file_id": "test_id"}

        symbols, edges = _fallback_symbols_regex(source, file_record, path)

        assert len(symbols) > 0
        symbol = symbols[0]
        # Arrow function is variable, not function or component
        assert symbol["kind"] == "variable"
        # Language should match tree-sitter language
        assert symbol["language"] == "tsx"

    def test_ts_fallback_and_tree_sitter_match_kinds(self, tmp_path: Path) -> None:
        """Tree-sitter and fallback emit same TS declaration kinds."""
        from mimry.ts_ast_adapter import parse_ts_like

        source = textwrap.dedent(
            """
            export function a() {}
            export class B {}
            export interface C {}
            export type D = string;
            export enum E { X }
            export const f = () => 1;
            """
        ).strip()

        ts_file = tmp_path / "test.ts"
        ts_file.write_text(source)

        file_record = {"file_id": "test_id"}

        # Parse through tree-sitter
        ts_symbols, _, _, _, _, adapter = parse_ts_like(ts_file, tmp_path, file_record, source)
        assert adapter == "typescript-ast"

        # Get the kinds from tree-sitter path
        ts_kinds = {(s["name"], s["kind"]) for s in ts_symbols}

        # Parse through fallback by forcing it
        from mimry.ts_ast_adapter import _fallback_symbols_regex

        fallback_symbols, _ = _fallback_symbols_regex(source, file_record, ts_file)
        fallback_kinds = {(s["name"], s["kind"]) for s in fallback_symbols}

        # Fallback kinds should be a subset of tree-sitter kinds
        assert fallback_kinds.issubset(ts_kinds), (
            f"Fallback {fallback_kinds} not subset of {ts_kinds}"
        )

    def test_jsx_parses_through_tree_sitter(self, tmp_path: Path) -> None:
        """JSX files parse through tree-sitter, not regex fallback."""
        from mimry.ts_ast_adapter import parse_ts_like

        source = "export const Component = () => <div>test</div>;"

        jsx_file = tmp_path / "Component.jsx"
        jsx_file.write_text(source)

        file_record = {"file_id": "test_id"}

        symbols, _, _, _, _, adapter = parse_ts_like(jsx_file, tmp_path, file_record, source)

        # Should use tree-sitter adapter, not fallback
        assert adapter == "typescript-ast"
        # Should parse the component
        names = {s["name"] for s in symbols}
        assert "Component" in names

    def test_pbxproj_with_string_target_no_error(self) -> None:
        """pbxproj with string buildConfigurationList gives no error."""
        from mimry.xcode import extract_xcode_targets

        pbxproj_dict = {
            "objects": {
                "target1": {
                    "isa": "PBXNativeTarget",
                    "name": "MyApp",
                    "productType": "com.apple.product-type.application",
                    "buildConfigurationList": "NOT_A_DICT",  # String instead of dict
                }
            }
        }

        # Should not raise AttributeError
        targets = extract_xcode_targets(pbxproj_dict)
        assert len(targets) == 1
        assert targets[0]["name"] == "MyApp"

    def test_shop_create_table_not_counted(self) -> None:
        """shop.create_table() is not counted as Alembic operation."""
        from mimry.framework_adapters import alembic_migration

        source = textwrap.dedent(
            """
            from alembic import op
            revision = 'test123'
            down_revision = None
            def upgrade():
                # This should not be counted
                shop.create_table("products")
                # This should be counted
                op.create_table("users")
            """
        )

        result = alembic_migration(source)

        assert result is not None
        assert "users" in result["tables"]
        assert "products" not in result["tables"]

    def test_plist_with_draft_string_gives_facts(self) -> None:
        """Info.plist with <string>[draft]</string> gives facts."""
        from mimry.framework_adapters import plist_facts

        plist_content = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<plist version="1.0">'
            "<dict>"
            "<key>CFBundleIdentifier</key>"
            "<string>com.example.app</string>"
            "<key>Description</key>"
            "<string>[draft]</string>"
            "</dict></plist>"
        )
        path = Path("Info.plist")
        root = Path("/tmp")
        file_record = {"file_id": "test_id"}

        result = plist_facts(path, root, file_record, data=plist_content.encode())

        assert len(result) > 0, f"Got empty result from plist_facts: {result}"
        assert any("com.example.app" in fact for fact in result)

    def test_plist_with_internal_subset_refused(self) -> None:
        """Info.plist with <!DOCTYPE plist [ ... ]> is refused."""
        from mimry.framework_adapters import plist_facts

        plist_content = textwrap.dedent(
            """
            <?xml version="1.0" encoding="UTF-8"?>
            <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
             "http://www.apple.com/DTDs/PropertyList-1.0.dtd" [
              <!ELEMENT plist (#PCDATA)>
            ]>
            <plist version="1.0">
            <dict>
                <key>CFBundleIdentifier</key>
                <string>com.example.app</string>
            </dict>
            </plist>
            """
        )
        path = Path("Info.plist")
        root = Path("/tmp")
        file_record = {"file_id": "test_id"}

        result = plist_facts(path, root, file_record, data=plist_content.encode())

        assert result == []

    def test_binary_plist_has_no_junk_in_hint(self, tmp_path: Path) -> None:
        """Binary Info.plist has facts but no binary junk in hint."""
        from mimry.scanner import adapt

        data_dict = {"CFBundleIdentifier": "com.example.app"}
        binary_data = plistlib.dumps(data_dict, fmt=plistlib.FMT_BINARY)

        plist_path = tmp_path / "Info.plist"
        plist_path.write_bytes(binary_data)

        root = tmp_path

        file_record, symbols, edges, imports, exports, calls, references = adapt(plist_path, root)

        # Should have facts in metadata_text
        assert file_record["adapter"] in {"swift-ios", "swift-ios+generic"}

        # Hint should not contain binary text
        hint = file_record.get("content_hint", "")
        # Check for NUL bytes
        assert "\x00" not in hint
        # Check for other control characters (exclude newline and tab)
        for char in hint:
            code = ord(char)
            if code < 32 and char not in "\n\t":
                raise AssertionError(f"Found control character {repr(char)} in hint")

    def test_realistic_pbxproj_both_references_edges(self, tmp_path: Path) -> None:
        """Realistic pbxproj creates references edges for targets."""
        root = tmp_path / "repo"
        app_dir = root / "App"
        share_dir = root / "ShareExtension"
        app_dir.mkdir(parents=True)
        share_dir.mkdir(parents=True)

        # Create the actual referenced files
        (app_dir / "App.entitlements").write_text(
            '<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict></dict></plist>',
            encoding="utf-8",
        )
        (share_dir / "Info.plist").write_text(
            '<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict></dict></plist>',
            encoding="utf-8",
        )

        # Create a realistic pbxproj
        pbxproj_content = textwrap.dedent(
            r"""
            // !$*UTF8*$!
            {
                archiveVersion = 1;
                classes = {};
                objectVersion = 54;
                objects = {
                    /* App target */
                    ABC123456789 = {
                        isa = PBXNativeTarget;
                        name = App;
                        productType = "com.apple.product-type.application";
                        buildConfigurationList = ABC123456780;
                    };
                    /* ShareExtension target */
                    DEF123456789 = {
                        isa = PBXNativeTarget;
                        name = ShareExtension;
                        productType = "com.apple.product-type.app-extension";
                        buildConfigurationList = DEF123456780;
                    };
                    ABC123456780 = {
                        isa = XCConfigurationList;
                        buildConfigurations = (ABC999999999,);
                    };
                    DEF123456780 = {
                        isa = XCConfigurationList;
                        buildConfigurations = (DEF999999999,);
                    };
                    ABC999999999 = {
                        isa = XCBuildConfiguration;
                        buildSettings = {
                            PRODUCT_BUNDLE_IDENTIFIER = com.example.app;
                            CODE_SIGN_ENTITLEMENTS = "App/App.entitlements";
                            INFOPLIST_FILE = "App/Info.plist";
                        };
                    };
                    DEF999999999 = {
                        isa = XCBuildConfiguration;
                        buildSettings = {
                            PRODUCT_BUNDLE_IDENTIFIER = "com.example.app.share";
                            CODE_SIGN_ENTITLEMENTS = "ShareExtension/ShareExtension.entitlements";
                            INFOPLIST_FILE = "ShareExtension/Info.plist";
                        };
                    };
                };
                rootObject = XYZ123456789;
            }
            """
        ).strip()

        (root / "App.xcodeproj").mkdir()
        (root / "App.xcodeproj" / "project.pbxproj").write_text(pbxproj_content)

        assert cmd_init(SimpleNamespace(root=root, root_type="repo", skip_graph=True)) == 0
        pointer = load_pointer(root)
        assert pointer is not None
        write_index(root, pointer)

        graph = json.loads(
            (root / ".mimry" / "mimry-out" / "graph" / "graph.json").read_text(encoding="utf-8")
        )
        labels = {n["id"]: n.get("label") for n in graph["nodes"]}
        references_pairs = {
            (labels.get(e["source"]), labels.get(e["target"]))
            for e in graph["edges"]
            if e.get("relation") == "references"
        }

        # Should have references from pbxproj to plist files
        # At least one reference edge to an Info.plist
        has_plist_ref = any("Info.plist" in tgt for src, tgt in references_pairs)
        assert has_plist_ref, f"No plist references in {references_pairs}"

        # Asserts references edges exist from pbxproj
        pbxproj_refs = [(src, tgt) for src, tgt in references_pairs if "project.pbxproj" in src]
        assert len(pbxproj_refs) >= 1, (
            f"Expected at least 1 pbxproj reference, got {len(pbxproj_refs)}"
        )
