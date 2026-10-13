"""Import resolution for new languages: C, C++, Ruby, Bash, PowerShell,
Lua and Perl.

Tests ensure resolve_imports correctly handles language-specific import
styles and extension handling, and that an import with no unambiguous
target produces no edge.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from mimry.commands import cmd_init, symbol_matches
from mimry.core.languages import extract
from mimry.core.resolve import resolve_imports
from mimry.indexer import write_index
from mimry.storage import load_pointer


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


class TestBashImports:
    """Bash source and dot paths: relative to the importer, exact."""

    def test_relative_source_resolves_next_to_importer(self):
        imports = {"scripts/build.sh": ["./lib/util.sh"]}
        rel_paths = {"scripts/build.sh", "scripts/lib/util.sh"}

        results = resolve_imports(imports, rel_paths)

        assert results == [
            {
                "importer": "scripts/build.sh",
                "target": "scripts/lib/util.sh",
                "confidence": "EXTRACTED",
            }
        ]

    def test_parent_directory_source(self):
        imports = {"scripts/sub/build.sh": ["../lib/util.sh"]}
        rel_paths = {"scripts/sub/build.sh", "scripts/lib/util.sh"}

        results = resolve_imports(imports, rel_paths)

        assert [r["target"] for r in results] == ["scripts/lib/util.sh"]

    def test_missing_relative_file_is_no_edge(self):
        """A file not at the path is not found by basename."""
        imports = {"scripts/build.sh": ["./util.sh"]}
        rel_paths = {"scripts/build.sh", "lib/util.sh"}

        assert resolve_imports(imports, rel_paths) == []

    def test_unexpanded_variable_is_no_edge(self):
        imports = {"scripts/build.sh": ["$DIR/util.sh", "${ROOT}/util.sh"]}
        rel_paths = {"scripts/build.sh", "scripts/util.sh"}

        assert resolve_imports(imports, rel_paths) == []


class TestPowerShellImports:
    """PowerShell dot-sourcing, Import-Module and `using module`."""

    def test_dot_source_resolves_next_to_importer(self):
        imports = {"tools/run.ps1": ["lib/Tools.ps1"]}
        rel_paths = {"tools/run.ps1", "tools/lib/Tools.ps1"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("tools/lib/Tools.ps1", "EXTRACTED")
        ]

    def test_backslash_path_resolves(self):
        imports = {"tools/run.ps1": ["lib\\Win.ps1"]}
        rel_paths = {"tools/run.ps1", "tools/lib/Win.ps1"}

        results = resolve_imports(imports, rel_paths)

        assert [r["target"] for r in results] == ["tools/lib/Win.ps1"]

    def test_module_path_resolves_relative_to_importer(self):
        imports = {"tools/run.ps1": ["../modules/Shape.psm1"]}
        rel_paths = {"tools/run.ps1", "modules/Shape.psm1"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("modules/Shape.psm1", "EXTRACTED")
        ]

    def test_bare_module_name_unique_repo_file_is_inferred(self):
        imports = {"tools/run.ps1": ["Helpers"]}
        rel_paths = {"tools/run.ps1", "modules/Helpers/Helpers.psm1"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("modules/Helpers/Helpers.psm1", "INFERRED")
        ]

    def test_bare_module_name_ambiguous_is_no_edge(self):
        imports = {"tools/run.ps1": ["Helpers"]}
        rel_paths = {"tools/run.ps1", "a/Helpers.psm1", "b/Helpers.psm1"}

        assert resolve_imports(imports, rel_paths) == []

    def test_bare_variable_path_is_no_edge(self):
        imports = {"tools/run.ps1": ["$HOME/x.ps1"]}
        rel_paths = {"tools/run.ps1", "tools/x.ps1"}

        assert resolve_imports(imports, rel_paths) == []

    def test_missing_dot_source_is_no_edge(self):
        imports = {"tools/run.ps1": ["./missing.ps1"]}
        rel_paths = {"tools/run.ps1"}

        assert resolve_imports(imports, rel_paths) == []


class TestLuaImports:
    """Lua `a.b` is `a/b.lua` or `a/b/init.lua`, as a path suffix."""

    def test_dotted_module_unique_suffix_is_inferred(self):
        imports = {"app/main.lua": ["app.util"]}
        rel_paths = {"app/main.lua", "lib/app/util.lua"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("lib/app/util.lua", "INFERRED")
        ]

    def test_init_lua_form_resolves(self):
        imports = {"app/main.lua": ["app.util"]}
        rel_paths = {"app/main.lua", "lib/app/util/init.lua"}

        results = resolve_imports(imports, rel_paths)

        assert [r["target"] for r in results] == ["lib/app/util/init.lua"]

    def test_match_under_importer_top_level_wins_when_unique(self):
        imports = {"src/main.lua": ["util"]}
        rel_paths = {"src/main.lua", "src/util.lua", "vendor/util.lua"}

        results = resolve_imports(imports, rel_paths)

        assert [r["target"] for r in results] == ["src/util.lua"]

    def test_ambiguous_suffix_is_no_edge(self):
        imports = {"src/main.lua": ["util"]}
        rel_paths = {"src/main.lua", "vendor/a/util.lua", "vendor/b/util.lua"}

        assert resolve_imports(imports, rel_paths) == []

    def test_non_lua_match_is_no_edge(self):
        imports = {"src/main.lua": ["json"]}
        rel_paths = {"src/main.lua", "config/json.yml"}

        assert resolve_imports(imports, rel_paths) == []


class TestPerlImports:
    """Perl `Foo::Bar` is `Foo/Bar.pm` as a unique path suffix."""

    def test_package_resolves_to_pm_under_lib(self):
        imports = {"bin/app.pl": ["Foo::Bar"]}
        rel_paths = {"bin/app.pl", "lib/Foo/Bar.pm"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("lib/Foo/Bar.pm", "INFERRED")
        ]

    def test_ambiguous_package_is_no_edge(self):
        imports = {"bin/app.pl": ["Foo::Bar"]}
        rel_paths = {"bin/app.pl", "lib/Foo/Bar.pm", "t/lib/Foo/Bar.pm"}

        assert resolve_imports(imports, rel_paths) == []

    def test_package_outside_repo_is_no_edge(self):
        imports = {"bin/app.pl": ["POSIX"]}
        rel_paths = {"bin/app.pl"}

        assert resolve_imports(imports, rel_paths) == []

    def test_pragma_is_not_an_import(self):
        """`use strict` and `use lib` never reach the resolver, so a
        strict.pm or lib.pm in the repo gets no edge.
        """
        extracted = extract("bin/app.pl", "use strict;\nuse lib 'lib';\n")
        assert extracted["imports"] == []

        imports = {"bin/app.pl": [i["module"] for i in extracted["imports"]]}
        rel_paths = {"bin/app.pl", "lib/strict.pm", "lib/lib.pm"}

        assert resolve_imports(imports, rel_paths) == []


def _write_repo(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def test_each_scripting_language_indexes_symbols_and_edges(tmp_path):
    """One file per scripting language: each has a function findable by
    name, and an import (or inheritance) edge is recorded for it.
    """
    root = tmp_path / "repo"
    _write_repo(
        root,
        {
            "ops/deploy.sh": "source ./common.sh\ndeploy_app() {\n  greet\n}\n",
            "ops/common.sh": "greet() {\n  echo hi\n}\n",
            "tools/Deploy.ps1": (
                ". $PSScriptRoot/Helpers.ps1\nfunction Invoke-Deploy {\n  Get-Helper\n}\n"
            ),
            "tools/Helpers.ps1": "function Get-Helper {\n}\n",
            "tools/Shapes.ps1": "class Shape {\n}\nclass Square : Shape {\n}\n",
            "game/main.lua": (
                'local util = require("game.util")\nfunction main()\n  util.render()\nend\n'
            ),
            "game/util.lua": "local M = {}\nfunction M.render()\nend\nreturn M\n",
            "scripts/app.pl": "use Acme::Widget;\nsub start {\n  Acme::Widget->draw();\n}\n1;\n",
            "lib/Acme/Widget.pm": (
                "package Acme::Widget;\nuse parent -norequire, 'Acme::Base';\nsub draw {\n}\n1;\n"
            ),
            "lib/Acme/Base.pm": "package Acme::Base;\nsub new {\n}\n1;\n",
        },
    )
    assert cmd_init(SimpleNamespace(root=root, root_type="repo", skip_graph=True)) == 0
    pointer = load_pointer(root)
    assert pointer is not None
    write_index(root, pointer)
    ptr = load_pointer(root)
    assert ptr is not None

    for name, language in [
        ("deploy_app", "bash"),
        ("Get-Helper", "powershell"),
        ("render", "lua"),
        ("draw", "perl"),
    ]:
        matches = symbol_matches(ptr, name)
        assert matches, f"{name} not found in the index"
        assert {m["language"] for m in matches} == {language}, matches

    graph = json.loads(
        (root / ".mimry" / "mimry-out" / "graph" / "graph.json").read_text(encoding="utf-8")
    )
    labels = {n["id"]: n.get("label") for n in graph["nodes"]}
    imports = {
        (labels.get(e["source"]), labels.get(e["target"]))
        for e in graph["edges"]
        if e["relation"] == "imports"
    }
    assert ("ops/deploy.sh", "ops/common.sh") in imports, sorted(imports)
    assert ("tools/Deploy.ps1", "tools/Helpers.ps1") in imports, sorted(imports)
    assert ("game/main.lua", "game/util.lua") in imports, sorted(imports)
    assert ("scripts/app.pl", "lib/Acme/Widget.pm") in imports, sorted(imports)

    inherits = {
        (labels.get(e["source"]), labels.get(e["target"]))
        for e in graph["edges"]
        if e["relation"] == "inherits"
    }
    assert ("Square", "Shape") in inherits, sorted(p for p in inherits if p[0])
    assert ("Acme::Widget", "Acme::Base") in inherits, sorted(p for p in inherits if p[0])
