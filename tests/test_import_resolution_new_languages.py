"""Import resolution for new languages: C, C++, Ruby.

Tests ensure resolve_imports correctly handles language-specific import
styles and extension handling.
"""

from mimry.core.resolve import resolve_imports


class TestCImports:
    """C/C++ #include resolution."""

    def test_c_quoted_include_exact(self):
        """C: ./utils.h with src/utils.h present resolves EXTRACTED."""
        imports = {"src/main.c": ["./utils.h"]}
        rel_paths = {"src/main.c", "src/utils.h"}

        results = resolve_imports(imports, rel_paths)

        assert len(results) == 1
        assert results[0]["target"] == "src/utils.h"
        assert results[0]["confidence"] == "EXTRACTED"

    def test_c_relative_include_parent_directory(self):
        """C: ../include/x.h resolves to include/x.h EXTRACTED."""
        imports = {"src/main.c": ["../include/x.h"]}
        rel_paths = {"src/main.c", "include/x.h"}

        results = resolve_imports(imports, rel_paths)

        assert len(results) == 1
        assert results[0]["target"] == "include/x.h"
        assert results[0]["confidence"] == "EXTRACTED"

    def test_c_include_inferred_from_absolute_path(self):
        """C: ./x.h with only include/x.h present infers INFERRED."""
        imports = {"src/main.c": ["./x.h"]}
        rel_paths = {"src/main.c", "include/x.h"}

        results = resolve_imports(imports, rel_paths)

        assert len(results) == 1
        assert results[0]["target"] == "include/x.h"
        assert results[0]["confidence"] == "INFERRED"

    def test_c_system_include_not_found(self):
        """C: <stdio.h> with no matching file resolves nothing."""
        imports = {"src/main.c": ["<stdio.h>"]}
        rel_paths = {"src/main.c"}

        results = resolve_imports(imports, rel_paths)

        assert len(results) == 0

    def test_c_system_include_with_subdir(self):
        """C: <foo/bar.h> with include/foo/bar.h infers INFERRED."""
        imports = {"src/main.c": ["<foo/bar.h>"]}
        rel_paths = {"src/main.c", "include/foo/bar.h"}

        results = resolve_imports(imports, rel_paths)

        assert len(results) == 1
        assert results[0]["target"] == "include/foo/bar.h"
        assert results[0]["confidence"] == "INFERRED"

    def test_c_include_ambiguous(self):
        """C: ./x.h with both a/x.h and b/x.h resolves nothing."""
        imports = {"src/main.c": ["./x.h"]}
        rel_paths = {"src/main.c", "a/x.h", "b/x.h"}

        results = resolve_imports(imports, rel_paths)

        assert len(results) == 0


class TestRubyImports:
    """Ruby require/require_relative resolution."""

    def test_ruby_relative_helper(self):
        """Ruby: ./helper with app/models/helper.rb present."""
        imports = {"app/models/user.rb": ["./helper"]}
        rel_paths = {"app/models/user.rb", "app/models/helper.rb"}

        results = resolve_imports(imports, rel_paths)

        assert len(results) == 1
        assert results[0]["target"] == "app/models/helper.rb"
        assert results[0]["confidence"] == "EXTRACTED"

    def test_ruby_relative_parent_directory(self):
        """Ruby: ../../lib/foo with lib/foo.rb present."""
        imports = {"app/models/user.rb": ["../../lib/foo"]}
        rel_paths = {"app/models/user.rb", "lib/foo.rb"}

        results = resolve_imports(imports, rel_paths)

        assert len(results) == 1
        assert results[0]["target"] == "lib/foo.rb"
        assert results[0]["confidence"] == "EXTRACTED"

    def test_ruby_bare_name_inferred(self):
        """Ruby: foo/bar with lib/foo/bar.rb infers INFERRED."""
        imports = {"app/models/user.rb": ["foo/bar"]}
        rel_paths = {"app/models/user.rb", "lib/foo/bar.rb"}

        results = resolve_imports(imports, rel_paths)

        assert len(results) == 1
        assert results[0]["target"] == "lib/foo/bar.rb"
        assert results[0]["confidence"] == "INFERRED"

    def test_ruby_json_stdlib_not_config_yml(self):
        """Ruby: json does not resolve to config/json.yml."""
        imports = {"app/models/user.rb": ["json"]}
        rel_paths = {"app/models/user.rb", "config/json.yml"}

        results = resolve_imports(imports, rel_paths)

        # Should not match; yml is not .rb
        assert len(results) == 0


class TestTypeScriptImports:
    """JavaScript/TypeScript: unchanged behavior."""

    def test_typescript_relative_import(self):
        """TS: ./util with web/util.ts present."""
        imports = {"web/app.ts": ["./util"]}
        rel_paths = {"web/app.ts", "web/util.ts"}

        results = resolve_imports(imports, rel_paths)

        assert len(results) == 1
        assert results[0]["target"] == "web/util.ts"
        assert results[0]["confidence"] == "EXTRACTED"
