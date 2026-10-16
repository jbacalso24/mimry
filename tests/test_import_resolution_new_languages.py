"""Import resolution for new languages: C, C++, Ruby, Bash, PowerShell,
Lua, Perl, Dart, Objective-C and Zig.

Tests ensure resolve_imports correctly handles language-specific import
styles and extension handling, and that an import with no unambiguous
target produces no edge.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mimry.commands import cmd_init
from mimry.core.languages import extract
from mimry.core.resolve import resolve_imports
from mimry.indexer import write_index
from mimry.queries import symbol_matches
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


class TestPythonAbsoluteImports:
    """Absolute Python imports resolve against src/ package roots."""

    def test_src_layout_module_resolves(self):
        imports = {"tests/test_main.py": ["pkg.util"]}
        rel_paths = {"tests/test_main.py", "src/pkg/__init__.py", "src/pkg/util.py"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("src/pkg/util.py", "EXTRACTED")
        ]

    def test_src_layout_package_resolves(self):
        imports = {"tests/test_main.py": ["pkg.sub"]}
        rel_paths = {"tests/test_main.py", "src/pkg/__init__.py", "src/pkg/sub/__init__.py"}

        results = resolve_imports(imports, rel_paths)

        assert [r["target"] for r in results] == ["src/pkg/sub/__init__.py"]

    def test_bare_src_layout_package_resolves(self):
        imports = {"tests/test_main.py": ["pkg"]}
        rel_paths = {"tests/test_main.py", "src/pkg/__init__.py"}

        results = resolve_imports(imports, rel_paths)

        assert [r["target"] for r in results] == ["src/pkg/__init__.py"]

    def test_repo_root_package_wins_over_src(self):
        imports = {"tests/test_main.py": ["pkg.util"]}
        rel_paths = {
            "tests/test_main.py",
            "pkg/__init__.py",
            "pkg/util.py",
            "src/pkg/__init__.py",
            "src/pkg/util.py",
        }

        results = resolve_imports(imports, rel_paths)

        assert [r["target"] for r in results] == ["pkg/util.py"]

    def test_folder_with_init_is_not_a_root(self):
        imports = {"tests/test_main.py": ["pkg.util"]}
        rel_paths = {
            "tests/test_main.py",
            "tools/__init__.py",
            "tools/pkg/__init__.py",
            "tools/pkg/util.py",
        }

        assert resolve_imports(imports, rel_paths) == []

    def test_src_root_wins_over_other_root(self):
        imports = {"tests/test_main.py": ["pkg.util"]}
        rel_paths = {
            "tests/test_main.py",
            "src/pkg/__init__.py",
            "src/pkg/util.py",
            "vendor/pkg/__init__.py",
            "vendor/pkg/util.py",
        }

        results = resolve_imports(imports, rel_paths)

        assert [r["target"] for r in results] == ["src/pkg/util.py"]

    def test_two_non_src_roots_is_no_edge(self):
        imports = {"tests/test_main.py": ["pkg.util"]}
        rel_paths = {
            "tests/test_main.py",
            "a/pkg/__init__.py",
            "a/pkg/util.py",
            "b/pkg/__init__.py",
            "b/pkg/util.py",
        }

        assert resolve_imports(imports, rel_paths) == []

    def test_stdlib_name_is_no_edge(self):
        imports = {"tests/test_main.py": ["os.path"]}
        rel_paths = {"tests/test_main.py", "src/pkg/__init__.py", "src/pkg/util.py"}

        assert resolve_imports(imports, rel_paths) == []

    def test_stdlib_name_inside_package_is_no_edge(self):
        """Issue 273: a bare name in a package is not a sibling."""
        imports = {"pkg/utils.py": ["types"]}
        rel_paths = {"pkg/__init__.py", "pkg/types.py", "pkg/utils.py"}

        assert resolve_imports(imports, rel_paths) == []

    def test_package_sibling_is_not_an_absolute_bare_import(self):
        imports = {"pkg/utils.py": ["helpers"]}
        rel_paths = {"pkg/__init__.py", "pkg/helpers.py", "pkg/utils.py"}

        assert resolve_imports(imports, rel_paths) == []

    def test_script_folder_bare_name_resolves_beside_importer(self):
        """A script folder (no __init__.py) resolves its sibling."""
        imports = {"scripts/run.py": ["helpers"]}
        rel_paths = {"scripts/run.py", "scripts/helpers.py"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("scripts/helpers.py", "INFERRED")
        ]

    def test_repo_top_level_package_with_stdlib_name_still_resolves(self):
        """A repo's own types/ package at the root still resolves."""
        imports = {"pkg/utils.py": ["types"]}
        rel_paths = {"pkg/__init__.py", "pkg/utils.py", "types/__init__.py"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("types/__init__.py", "EXTRACTED")
        ]

    def test_src_layout_package_with_stdlib_name_still_resolves(self):
        imports = {"src/pkg/utils.py": ["types"]}
        rel_paths = {"src/pkg/__init__.py", "src/pkg/utils.py", "src/types/__init__.py"}

        results = resolve_imports(imports, rel_paths)

        assert [r["target"] for r in results] == ["src/types/__init__.py"]

    def test_python_relative_forms_are_unchanged(self):
        """Relative forms keep their leading dots and still resolve."""
        rel_paths = {
            "pkg/__init__.py",
            "pkg/types.py",
            "pkg/utils.py",
            "pkg/sub/__init__.py",
            "pkg/sub/a.py",
            "pkg/x.py",
        }
        imports = {
            "pkg/types.py": [".utils", ".utils.helper"],
            "pkg/sub/a.py": ["..x"],
        }

        results = resolve_imports(imports, rel_paths)

        assert [(r["importer"], r["target"], r["confidence"]) for r in results] == [
            ("pkg/sub/a.py", "pkg/x.py", "EXTRACTED"),
            ("pkg/types.py", "pkg/utils.py", "EXTRACTED"),
        ]

    def test_dotted_absolute_import_inside_package_is_unchanged(self):
        imports = {"pkg/types.py": ["pkg.utils"]}
        rel_paths = {"pkg/__init__.py", "pkg/types.py", "pkg/utils.py"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [("pkg/utils.py", "EXTRACTED")]


class TestTypeScriptDirectoryImports:
    """Issue 275: `from '.'` and `from '..'` load the folder's index."""

    @pytest.mark.parametrize("index", ["index.ts", "index.tsx", "index.js"])
    def test_dot_resolves_to_index_beside_importer(self, index):
        imports = {"src/mw/index.test.ts": ["."]}
        rel_paths = {"src/mw/index.test.ts", f"src/mw/{index}"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [
            (f"src/mw/{index}", "EXTRACTED")
        ]

    @pytest.mark.parametrize("specifier", ["..", "../"])
    def test_dotdot_resolves_to_parent_index(self, specifier):
        imports = {"src/mw/sub/a.ts": [specifier]}
        rel_paths = {"src/mw/sub/a.ts", "src/mw/index.ts"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("src/mw/index.ts", "EXTRACTED")
        ]

    def test_dot_slash_resolves_to_index(self):
        imports = {"src/mw/index.test.ts": ["./"]}
        rel_paths = {"src/mw/index.test.ts", "src/mw/index.ts"}

        results = resolve_imports(imports, rel_paths)

        assert [r["target"] for r in results] == ["src/mw/index.ts"]

    def test_dot_at_repository_root_resolves_to_root_index(self):
        imports = {"index.test.ts": ["."]}
        rel_paths = {"index.test.ts", "index.ts"}

        results = resolve_imports(imports, rel_paths)

        assert [r["target"] for r in results] == ["index.ts"]

    def test_directory_without_index_is_no_edge(self):
        imports = {"src/mw/a.ts": [".", "..", "./", "../"]}
        rel_paths = {"src/mw/a.ts", "src/b.ts"}

        assert resolve_imports(imports, rel_paths) == []

    def test_trailing_slash_does_not_match_a_sibling_file(self):
        """A trailing slash names a folder, not a sibling src/mw.ts."""
        imports = {"src/mw/index.test.ts": ["./"]}
        rel_paths = {"src/mw/index.test.ts", "src/mw.ts"}

        assert resolve_imports(imports, rel_paths) == []

    def test_dot_in_python_stays_no_edge(self):
        imports = {"pkg/types.py": ["."]}
        rel_paths = {"pkg/__init__.py", "pkg/types.py"}

        assert resolve_imports(imports, rel_paths) == []


def test_src_layout_repo_resolves_imports_and_calls(tmp_path):
    """Issue 161 repro: a src/ layout package imported by absolute name.

    The import edge from src/pkg/main.py to src/pkg/util.py, the test's
    import of pkg.main, and the call from run to helper must all exist.
    """
    root = tmp_path / "repo"
    _write_repo(
        root,
        {
            "pyproject.toml": '[project]\nname = "pkg"\nversion = "0.1.0"\n',
            "src/pkg/__init__.py": "",
            "src/pkg/util.py": "def helper():\n    return 1\n",
            "src/pkg/main.py": (
                "from pkg.util import helper\n\n\ndef run():\n    return helper()\n"
            ),
            "tests/test_main.py": (
                "from pkg.main import run\n\n\ndef test_run():\n    assert run() == 1\n"
            ),
        },
    )
    assert cmd_init(SimpleNamespace(root=root, root_type="repo", skip_graph=True)) == 0
    pointer = load_pointer(root)
    assert pointer is not None
    write_index(root, pointer)

    graph = json.loads(
        (root / ".mimry" / "mimry-out" / "graph" / "graph.json").read_text(encoding="utf-8")
    )
    labels = {n["id"]: n.get("label") for n in graph["nodes"]}
    imports = {
        (labels.get(e["source"]), labels.get(e["target"]))
        for e in graph["edges"]
        if e["relation"] == "imports"
    }
    assert ("src/pkg/main.py", "src/pkg/util.py") in imports, sorted(imports)
    assert ("tests/test_main.py", "src/pkg/main.py") in imports, sorted(imports)

    calls = {
        (labels.get(e["source"]), labels.get(e["target"]))
        for e in graph["edges"]
        if e["relation"] == "calls"
    }
    assert ("run", "helper") in calls, sorted(calls)


class TestDartImports:
    """Dart URIs: a relative one beside the importer, `package:` through
    the one pubspec of that name, and `dart:` never.
    """

    def test_relative_uri_beside_the_importer(self):
        imports = {"lib/main.dart": ["src/models.dart"]}
        rel_paths = {"lib/main.dart", "lib/src/models.dart"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("lib/src/models.dart", "EXTRACTED")
        ]

    def test_package_uri_resolves_into_the_pubspec_lib_dir(self):
        imports = {"app/lib/main.dart": ["package:app/src/models.dart"]}
        rel_paths = {"app/lib/main.dart", "app/lib/src/models.dart"}

        results = resolve_imports(imports, rel_paths, dart_packages={"app": ["app"]})

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("app/lib/src/models.dart", "INFERRED")
        ]

    def test_two_pubspecs_with_one_name_is_no_edge(self):
        imports = {"app/lib/main.dart": ["package:app/src/models.dart"]}
        rel_paths = {"app/lib/main.dart", "app/lib/src/models.dart", "copy/lib/src/models.dart"}

        results = resolve_imports(imports, rel_paths, dart_packages={"app": ["app", "copy"]})

        assert results == []

    def test_package_without_a_pubspec_is_no_edge(self):
        imports = {"app/lib/main.dart": ["package:http/client.dart"]}
        rel_paths = {"app/lib/main.dart", "http/lib/client.dart"}

        assert resolve_imports(imports, rel_paths, dart_packages={"app": ["app"]}) == []

    def test_missing_relative_file_is_no_edge(self):
        imports = {"lib/main.dart": ["src/missing.dart"]}
        rel_paths = {"lib/main.dart"}

        assert resolve_imports(imports, rel_paths) == []

    def test_dart_scheme_never_resolves(self):
        imports = {"lib/main.dart": ["dart:async"]}
        rel_paths = {"lib/main.dart", "lib/async.dart"}

        assert resolve_imports(imports, rel_paths) == []


class TestObjCImports:
    """Objective-C includes follow the C rules; `@import Foo;` never
    resolves.
    """

    def test_quoted_include_next_to_the_importer(self):
        imports = {"src/Widget.m": ["./Widget.h"]}
        rel_paths = {"src/Widget.m", "src/Widget.h"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [("src/Widget.h", "EXTRACTED")]

    def test_angle_include_by_unique_path_suffix(self):
        imports = {"src/Widget.m": ["<UIKit/UIKit.h>"]}
        rel_paths = {"src/Widget.m", "vendor/UIKit/UIKit.h"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("vendor/UIKit/UIKit.h", "INFERRED")
        ]

    def test_framework_module_is_no_edge_even_with_a_matching_header(self):
        imports = {"src/Widget.m": ["Foundation"]}
        rel_paths = {"src/Widget.m", "src/Foundation.h"}

        assert resolve_imports(imports, rel_paths) == []


class TestZigImports:
    """Zig `@import("x.zig")` resolves beside the importer; package
    names never do.
    """

    def test_zig_file_beside_the_importer(self):
        imports = {"src/main.zig": ["util.zig"]}
        rel_paths = {"src/main.zig", "src/util.zig"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [("src/util.zig", "EXTRACTED")]

    def test_package_name_is_no_edge_even_with_a_matching_file(self):
        imports = {"src/main.zig": ["std"]}
        rel_paths = {"src/main.zig", "lib/std.zig"}

        assert resolve_imports(imports, rel_paths) == []

    def test_missing_zig_file_is_no_edge(self):
        imports = {"src/main.zig": ["other.zig"]}
        rel_paths = {"src/main.zig"}

        assert resolve_imports(imports, rel_paths) == []


class TestGoImports:
    """A Go import names a package: every non-test .go file in the
    folder that its go.mod module path maps to. The standard library
    and third-party paths name nothing.
    """

    def test_import_links_every_file_of_the_package(self):
        imports = {"main.go": ["example.com/app/lib"]}
        rel_paths = {"main.go", "lib/lib.go", "lib/util.go", "other/x.go"}

        results = resolve_imports(imports, rel_paths, go_modules={"example.com/app": [""]})

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("lib/lib.go", "EXTRACTED"),
            ("lib/util.go", "EXTRACTED"),
        ]

    def test_test_files_are_not_linked(self):
        imports = {"main.go": ["example.com/app/lib"]}
        rel_paths = {"main.go", "lib/lib.go", "lib/lib_test.go"}

        results = resolve_imports(imports, rel_paths, go_modules={"example.com/app": [""]})

        assert [r["target"] for r in results] == ["lib/lib.go"]

    def test_standard_library_import_is_no_edge_even_with_a_matching_file(self):
        imports = {"main.go": ["fmt"]}
        rel_paths = {"main.go", "util/fmt.go"}

        assert resolve_imports(imports, rel_paths, go_modules={"example.com/app": [""]}) == []

    def test_without_a_go_mod_nothing_resolves(self):
        imports = {"main.go": ["example.com/app/lib"]}
        rel_paths = {"main.go", "lib/lib.go"}

        assert resolve_imports(imports, rel_paths) == []

    def test_longest_module_path_wins_for_a_nested_module(self):
        imports = {"main.go": ["example.com/app/tools/gen"]}
        rel_paths = {"main.go", "tools/gen/gen.go", "tools/gen.go"}

        results = resolve_imports(
            imports,
            rel_paths,
            go_modules={"example.com/app": [""], "example.com/app/tools": ["tools"]},
        )

        assert [r["target"] for r in results] == ["tools/gen/gen.go"]

    def test_go_mod_in_a_subfolder_maps_its_own_packages(self):
        imports = {"services/api/main.go": ["example.com/api/internal/db"]}
        rel_paths = {"services/api/main.go", "services/api/internal/db/db.go"}

        results = resolve_imports(
            imports, rel_paths, go_modules={"example.com/api": ["services/api"]}
        )

        assert [r["target"] for r in results] == ["services/api/internal/db/db.go"]

    def test_module_declared_twice_is_no_edge(self):
        imports = {"main.go": ["example.com/app/lib"]}
        rel_paths = {"main.go", "lib/lib.go"}

        assert (
            resolve_imports(imports, rel_paths, go_modules={"example.com/app": ["", "copy"]}) == []
        )


class TestRustImports:
    """Rust `use` and `mod` paths.

    crate:: starts at the crate root, self:: and super:: at the module
    the importer belongs to, and `mod x;` names the file beside it.
    """

    def test_crate_path_links_the_module_file(self):
        imports = {"src/main.rs": ["crate::b::helper"]}
        rel_paths = {"src/main.rs", "src/b.rs"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [("src/b.rs", "EXTRACTED")]

    def test_crate_path_to_a_mod_rs_folder(self):
        imports = {"src/main.rs": ["crate::a::b::Item"]}
        rel_paths = {"src/main.rs", "src/a/b/mod.rs"}

        results = resolve_imports(imports, rel_paths)

        assert [r["target"] for r in results] == ["src/a/b/mod.rs"]

    def test_crate_root_is_the_nearest_lib_rs_folder(self):
        imports = {"crates/app/src/lib.rs": ["crate::util"]}
        rel_paths = {"crates/app/src/lib.rs", "crates/app/src/util.rs", "src/util.rs"}

        results = resolve_imports(imports, rel_paths)

        assert [r["target"] for r in results] == ["crates/app/src/util.rs"]

    def test_mod_declaration_in_main_rs_is_a_sibling_file(self):
        imports = {"src/main.rs": ["b"]}
        rel_paths = {"src/main.rs", "src/b.rs"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [("src/b.rs", "EXTRACTED")]

    def test_mod_declaration_in_a_named_file_uses_its_folder(self):
        imports = {"src/foo.rs": ["bar"]}
        rel_paths = {"src/foo.rs", "src/foo/bar.rs"}

        results = resolve_imports(imports, rel_paths)

        assert [r["target"] for r in results] == ["src/foo/bar.rs"]

    def test_super_and_self_paths(self):
        imports = {"src/foo/mod.rs": ["super::util", "self::inner"]}
        rel_paths = {"src/foo/mod.rs", "src/util.rs", "src/foo/inner.rs"}

        results = resolve_imports(imports, rel_paths)

        assert sorted(r["target"] for r in results) == ["src/foo/inner.rs", "src/util.rs"]

    def test_missing_module_file_is_no_edge(self):
        imports = {"src/main.rs": ["crate::missing::f"]}
        rel_paths = {"src/main.rs"}

        assert resolve_imports(imports, rel_paths) == []

    def test_external_crate_path_is_no_edge(self):
        imports = {"src/main.rs": ["std::fmt"]}
        rel_paths = {"src/main.rs"}

        assert resolve_imports(imports, rel_paths) == []

    def test_extraction_keeps_use_paths_and_bodyless_mods_only(self):
        source = 'mod b;\nmod inline { fn f() { let s = "x"; } }\nuse crate::b::helper;\n'

        modules = [i["module"] for i in extract("src/main.rs", source)["imports"]]

        assert sorted(modules) == ["b", "crate::b::helper"]


class TestPhpImports:
    """PHP require/include of a literal path.

    The path resolves beside the importing file, as require_relative
    does. A variable path names nothing.
    """

    def test_literal_forms_are_extracted_as_relative_paths(self):
        source = (
            "<?php\nrequire_once 'b.php';\nrequire (__DIR__ . '/x.php');\n"
            "include \"c.php\";\ninclude $dyn;\nrequire $base . 'd.php';\n"
        )

        modules = {i["module"] for i in extract("a.php", source)["imports"]}

        assert modules == {"./b.php", "./x.php", "./c.php"}

    def test_literal_paths_resolve_beside_the_importer(self):
        imports = {"app/a.php": ["./b.php", "../lib/c.php"]}
        rel_paths = {"app/a.php", "app/b.php", "lib/c.php"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("app/b.php", "EXTRACTED"),
            ("lib/c.php", "EXTRACTED"),
        ]

    def test_missing_file_is_no_edge(self):
        assert resolve_imports({"app/a.php": ["./missing.php"]}, {"app/a.php"}) == []

    def test_no_javascript_extension_is_probed(self):
        imports = {"app/a.php": ["./b"]}
        rel_paths = {"app/a.php", "app/b.js"}

        assert resolve_imports(imports, rel_paths) == []


def test_each_new_app_language_indexes_symbols_and_edges(tmp_path):
    """One file per language: symbols are findable by name, and each
    language gets an import edge. Dart's package: import resolves
    through the pubspec.yaml that names its package.
    """
    root = tmp_path / "repo"
    _write_repo(
        root,
        {
            "flutter_app/pubspec.yaml": "name: app\n",
            "flutter_app/lib/src/models.dart": "class User {\n  String name = '';\n}\n",
            "flutter_app/lib/main.dart": (
                "import 'package:app/src/models.dart';\nvoid startApp() {\n  User();\n}\n"
            ),
            "native/Widget.m": (
                '#import "Widget.h"\n@implementation Widget\n- (void)render {\n}\n@end\n'
            ),
            "native/Widget.h": "@interface Widget : NSObject\n- (void)render;\n@end\n",
            "tools/gen.zig": (
                'const util = @import("util.zig");\npub fn generate() void {\n    util.run();\n}\n'
            ),
            "tools/util.zig": "pub fn run() void {}\n",
        },
    )
    assert cmd_init(SimpleNamespace(root=root, root_type="repo", skip_graph=True)) == 0
    pointer = load_pointer(root)
    assert pointer is not None
    write_index(root, pointer)
    ptr = load_pointer(root)
    assert ptr is not None

    for name, language in [
        ("User", "dart"),
        ("render", "objc"),
        ("generate", "zig"),
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
    assert ("flutter_app/lib/main.dart", "flutter_app/lib/src/models.dart") in imports, sorted(
        imports
    )
    assert ("native/Widget.m", "native/Widget.h") in imports, sorted(imports)
    assert ("tools/gen.zig", "tools/util.zig") in imports, sorted(imports)


def test_go_rust_and_php_imports_link_files_end_to_end(tmp_path):
    """Go packages, Rust crate and mod paths, and PHP require_once each
    give an import edge, so a call into the other file can resolve.
    """
    root = tmp_path / "repo"
    _write_repo(
        root,
        {
            "go.mod": "module example.com/app\n\ngo 1.21\n",
            "main.go": (
                'package main\n\nimport "example.com/app/lib"\n\n'
                "func Run() int {\n\treturn lib.Helper()\n}\n"
            ),
            "lib/lib.go": "package lib\n\nfunc Helper() int {\n\treturn 1\n}\n",
            "src/main.rs": "use crate::b::helper;\n\nfn run() -> i32 {\n    helper()\n}\n",
            "src/b.rs": "pub fn helper() -> i32 {\n    1\n}\n",
            "a.php": "<?php\nrequire_once 'b.php';\n\nfunction run() {\n    return helper();\n}\n",
            "b.php": "<?php\nfunction helper() {\n    return 1;\n}\n",
        },
    )
    assert cmd_init(SimpleNamespace(root=root, root_type="repo", skip_graph=True)) == 0
    pointer = load_pointer(root)
    assert pointer is not None
    write_index(root, pointer)

    graph = json.loads(
        (root / ".mimry" / "mimry-out" / "graph" / "graph.json").read_text(encoding="utf-8")
    )
    labels = {n["id"]: n.get("label") for n in graph["nodes"]}
    imports = {
        (labels.get(e["source"]), labels.get(e["target"]))
        for e in graph["edges"]
        if e["relation"] == "imports"
    }
    assert ("main.go", "lib/lib.go") in imports, sorted(imports)
    assert ("src/main.rs", "src/b.rs") in imports, sorted(imports)
    assert ("a.php", "b.php") in imports, sorted(imports)


def _graph_pairs(root, relation):
    graph = json.loads(
        (root / ".mimry" / "mimry-out" / "graph" / "graph.json").read_text(encoding="utf-8")
    )
    labels = {n["id"]: n.get("label") for n in graph["nodes"]}
    return {
        (labels.get(e["source"]), labels.get(e["target"]))
        for e in graph["edges"]
        if e["relation"] == relation
    }


def _index_repo(root, files):
    _write_repo(root, files)
    assert cmd_init(SimpleNamespace(root=root, root_type="repo", skip_graph=True)) == 0
    pointer = load_pointer(root)
    assert pointer is not None
    write_index(root, pointer)


def test_zig_call_inside_a_function_reaches_the_graph(tmp_path):
    """Zig: a call in a function body belongs to that function, so the
    cross-file call becomes a graph edge.
    """
    root = tmp_path / "repo"
    _index_repo(
        root,
        {
            "b.zig": "pub fn helper() i32 {\n    return 1;\n}\n",
            "a.zig": (
                'const b = @import("b.zig");\n\npub fn run() i32 {\n    return b.helper();\n}\n'
            ),
        },
    )
    assert ("run", "helper") in _graph_pairs(root, "calls")


def test_rust_qualified_call_reaches_the_graph(tmp_path):
    """Rust: `Foo::new()` is a call to `new`, which the impl block
    defines in the same file.
    """
    root = tmp_path / "repo"
    _index_repo(
        root,
        {
            "src/main.rs": (
                "struct Foo;\n\nimpl Foo {\n    fn new() -> Foo {\n        Foo\n    }\n}\n\n"
                "fn run() -> Foo {\n    Foo::new()\n}\n"
            ),
        },
    )
    assert ("run", "new") in _graph_pairs(root, "calls")


class TestElixirImports:
    """Elixir `Foo.BarBaz` is `foo/bar_baz.ex`, found by its path under
    any folder, and only when exactly one file has that path.
    """

    def test_module_resolves_to_its_snake_case_file(self):
        imports = {"lib/app/web.ex": ["MyApp.Repo"]}
        rel_paths = {"lib/app/web.ex", "lib/my_app/repo.ex"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("lib/my_app/repo.ex", "INFERRED")
        ]

    def test_acronym_module_snake_cases_like_elixir(self):
        imports = {"lib/app.ex": ["MyApp.HTTPServer"]}
        rel_paths = {"lib/app.ex", "lib/my_app/http_server.ex"}

        results = resolve_imports(imports, rel_paths)

        assert [r["target"] for r in results] == ["lib/my_app/http_server.ex"]

    def test_library_module_is_no_edge(self):
        imports = {"lib/app/web.ex": ["Ecto.Query"]}
        rel_paths = {"lib/app/web.ex", "lib/query.ex"}

        assert resolve_imports(imports, rel_paths) == []

    def test_two_files_at_one_path_is_no_edge(self):
        imports = {"lib/app.ex": ["Shared.Repo"]}
        rel_paths = {"lib/app.ex", "lib/shared/repo.ex", "old/shared/repo.ex"}

        assert resolve_imports(imports, rel_paths) == []


class TestHaskellImports:
    """Haskell `X.Y` is `X/Y.hs` under any source root."""

    def test_module_under_src_resolves(self):
        imports = {"app/Main.hs": ["Data.Shapes.Area"]}
        rel_paths = {"app/Main.hs", "src/Data/Shapes/Area.hs"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("src/Data/Shapes/Area.hs", "INFERRED")
        ]

    def test_module_at_the_repo_root_resolves(self):
        imports = {"app/Main.hs": ["Geometry.Point"]}
        rel_paths = {"app/Main.hs", "Geometry/Point.hs"}

        results = resolve_imports(imports, rel_paths)

        assert [r["target"] for r in results] == ["Geometry/Point.hs"]

    def test_package_module_is_no_edge(self):
        imports = {"app/Main.hs": ["Data.Map"]}
        rel_paths = {"app/Main.hs", "src/Data/Shapes/Area.hs"}

        assert resolve_imports(imports, rel_paths) == []

    def test_ambiguous_module_is_no_edge(self):
        imports = {"app/Main.hs": ["Data.Map"]}
        rel_paths = {"app/Main.hs", "src/Data/Map.hs", "lib/Data/Map.hs"}

        assert resolve_imports(imports, rel_paths) == []


class TestOCamlImports:
    """OCaml module `Foo_bar` is `foo_bar.ml` anywhere in the repo, or
    its `.mli` when only that exists.
    """

    def test_module_resolves_to_its_file(self):
        imports = {"bin/main.ml": ["Util_helpers"]}
        rel_paths = {"bin/main.ml", "src/util_helpers.ml"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("src/util_helpers.ml", "INFERRED")
        ]

    def test_interface_only_module_resolves_to_its_mli(self):
        imports = {"bin/main.ml": ["Store"]}
        rel_paths = {"bin/main.ml", "src/store.mli"}

        results = resolve_imports(imports, rel_paths)

        assert [r["target"] for r in results] == ["src/store.mli"]

    def test_stdlib_module_is_no_edge(self):
        imports = {"bin/main.ml": ["Printf"]}
        rel_paths = {"bin/main.ml"}

        assert resolve_imports(imports, rel_paths) == []

    def test_dotted_module_is_no_edge(self):
        imports = {"bin/main.ml": ["Core.Printf"]}
        rel_paths = {"bin/main.ml", "src/core/printf.ml"}

        assert resolve_imports(imports, rel_paths) == []


class TestRImports:
    """`source("x.R")` is relative to the importing file, then to the
    repo root. A package named by library() never resolves.
    """

    def test_source_resolves_beside_the_importing_file(self):
        imports = {"analysis/main.R": ["R/helpers.R"]}
        rel_paths = {"analysis/main.R", "analysis/R/helpers.R"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("analysis/R/helpers.R", "EXTRACTED")
        ]

    def test_source_falls_back_to_the_repo_root(self):
        imports = {"analysis/main.R": ["R/helpers.R"]}
        rel_paths = {"analysis/main.R", "R/helpers.R"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [("R/helpers.R", "INFERRED")]

    def test_library_package_is_no_edge(self):
        imports = {"analysis/main.R": ["dplyr"]}
        rel_paths = {"analysis/main.R", "analysis/dplyr.R"}

        assert resolve_imports(imports, rel_paths) == []

    def test_missing_source_file_is_no_edge(self):
        imports = {"analysis/main.R": ["R/missing.R"]}
        rel_paths = {"analysis/main.R"}

        assert resolve_imports(imports, rel_paths) == []


class TestJuliaImports:
    """`include("x.jl")` is relative to the importing file. `using` and
    `import` name packages, which never resolve.
    """

    def test_include_resolves_beside_the_importer(self):
        imports = {"src/Geometry.jl": ["shapes.jl"]}
        rel_paths = {"src/Geometry.jl", "src/shapes.jl"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("src/shapes.jl", "EXTRACTED")
        ]

    def test_using_a_package_is_no_edge_even_with_a_matching_file(self):
        imports = {"src/Geometry.jl": ["LinearAlgebra"]}
        rel_paths = {"src/Geometry.jl", "src/LinearAlgebra.jl"}

        assert resolve_imports(imports, rel_paths) == []

    def test_missing_include_is_no_edge(self):
        imports = {"src/Geometry.jl": ["missing.jl"]}
        rel_paths = {"src/Geometry.jl"}

        assert resolve_imports(imports, rel_paths) == []


def test_each_functional_language_indexes_symbols_and_edges(tmp_path):
    """One small project per functional or scientific language: each
    function is findable by name, and each language gets its import
    edge, including the Elixir, Haskell, OCaml, R and Julia rules.
    """
    root = tmp_path / "repo"
    _index_repo(
        root,
        {
            "web/lib/my_app/repo.ex": (
                "defmodule MyApp.Repo do\n  def insert(user) do\n    user\n  end\nend\n"
            ),
            "web/lib/app/web.ex": (
                "defmodule App.Web do\n  alias MyApp.Repo\n\n"
                "  def render(user) do\n    Repo.insert(user)\n  end\nend\n"
            ),
            "hs/src/Data/Shapes/Area.hs": (
                "module Data.Shapes.Area where\n\narea :: Double -> Double\narea r = r * r\n"
            ),
            "hs/app/Main.hs": (
                "module Main where\n\nimport Data.Shapes.Area\n\nmain = print (area 2)\n"
            ),
            "ml/util_helpers.ml": 'let shout s = s ^ "!"\n',
            "ml/bin.ml": 'open Util_helpers\nlet run () = shout "hi"\n',
            "stats/R/helpers.R": "scale_col <- function(x) x / 2\n",
            "stats/main.R": 'source("R/helpers.R")\nresult <- scale_col(3)\n',
            "jl/src/shapes.jl": "circle_area(r) = pi * r^2\n",
            "jl/src/Geometry.jl": 'include("shapes.jl")\nperimeter(r) = 2pi * r\n',
        },
    )
    pointer = load_pointer(root)
    assert pointer is not None

    for name, language in [
        ("render", "elixir"),
        ("area", "haskell"),
        ("shout", "ocaml"),
        ("scale_col", "r"),
        ("circle_area", "julia"),
        ("perimeter", "julia"),
    ]:
        matches = symbol_matches(pointer, name)
        assert matches, f"{name} not found in the index"
        exact = {m["language"] for m in matches if m["name"] == name}
        assert exact == {language}, matches

    imports = _graph_pairs(root, "imports")
    assert ("web/lib/app/web.ex", "web/lib/my_app/repo.ex") in imports, sorted(imports)
    assert ("hs/app/Main.hs", "hs/src/Data/Shapes/Area.hs") in imports, sorted(imports)
    assert ("ml/bin.ml", "ml/util_helpers.ml") in imports, sorted(imports)
    assert ("stats/main.R", "stats/R/helpers.R") in imports, sorted(imports)
    assert ("jl/src/Geometry.jl", "jl/src/shapes.jl") in imports, sorted(imports)


class TestSolidityImports:
    """`./x.sol` is relative to the importing file. Another path is
    tried from the repo root, then node_modules/, lib/, and a Foundry
    library's src/. remappings.txt is not read.
    """

    def test_relative_import_resolves_beside_the_importer(self):
        imports = {"src/Vault.sol": ["./IERC20.sol"]}
        rel_paths = {"src/Vault.sol", "src/IERC20.sol"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("src/IERC20.sol", "EXTRACTED")
        ]

    def test_package_import_resolves_under_node_modules(self):
        imports = {"src/Vault.sol": ["@openzeppelin/contracts/access/Ownable.sol"]}
        rel_paths = {
            "src/Vault.sol",
            "node_modules/@openzeppelin/contracts/access/Ownable.sol",
        }

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("node_modules/@openzeppelin/contracts/access/Ownable.sol", "INFERRED")
        ]

    def test_package_import_falls_back_to_lib(self):
        imports = {"src/Vault.sol": ["solmate/src/auth/Owned.sol"]}
        rel_paths = {"src/Vault.sol", "lib/solmate/src/auth/Owned.sol"}

        results = resolve_imports(imports, rel_paths)

        assert [r["target"] for r in results] == ["lib/solmate/src/auth/Owned.sol"]

    def test_foundry_library_resolves_under_its_src(self):
        imports = {"test/Vault.t.sol": ["forge-std/Test.sol"]}
        rel_paths = {"test/Vault.t.sol", "lib/forge-std/src/Test.sol"}

        results = resolve_imports(imports, rel_paths)

        assert [r["target"] for r in results] == ["lib/forge-std/src/Test.sol"]

    def test_root_relative_import_resolves_from_the_repo_root(self):
        imports = {"contracts/Vault.sol": ["contracts/lib/Math.sol"]}
        rel_paths = {"contracts/Vault.sol", "contracts/lib/Math.sol"}

        results = resolve_imports(imports, rel_paths)

        assert [r["target"] for r in results] == ["contracts/lib/Math.sol"]

    def test_renamed_prefix_is_no_edge(self):
        """`@oz/` only reaches lib/openzeppelin-contracts through
        remappings.txt, which is not read, so no edge is made.
        """
        imports = {"src/Vault.sol": ["@oz/token/ERC20.sol"]}
        rel_paths = {"src/Vault.sol", "lib/openzeppelin-contracts/contracts/token/ERC20.sol"}

        assert resolve_imports(imports, rel_paths) == []

    def test_missing_relative_import_is_no_edge(self):
        imports = {"src/Vault.sol": ["./Missing.sol"]}
        rel_paths = {"src/Vault.sol"}

        assert resolve_imports(imports, rel_paths) == []


class TestFortranImports:
    """`use name` is the one file that defines `module name`, compared
    without case. An include path is relative to the importing file.
    """

    def test_use_resolves_to_the_file_that_defines_the_module(self):
        imports = {"app/main.f90": ["Geometry"]}
        rel_paths = {"app/main.f90", "src/shapes.f90"}
        modules = {"geometry": ["src/shapes.f90"]}

        results = resolve_imports(imports, rel_paths, fortran_modules=modules)

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("src/shapes.f90", "INFERRED")
        ]

    def test_intrinsic_module_never_resolves(self):
        imports = {"app/main.f90": ["iso_fortran_env", "ieee_arithmetic"]}
        rel_paths = {"app/main.f90", "src/env.f90"}
        modules = {"iso_fortran_env": ["src/env.f90"]}

        assert resolve_imports(imports, rel_paths, fortran_modules=modules) == []

    def test_two_files_defining_one_module_is_no_edge(self):
        imports = {"app/main.f90": ["geometry"]}
        rel_paths = {"app/main.f90", "a/shapes.f90", "b/shapes.f90"}
        modules = {"geometry": ["a/shapes.f90", "b/shapes.f90"]}

        assert resolve_imports(imports, rel_paths, fortran_modules=modules) == []

    def test_unknown_module_is_no_edge(self):
        imports = {"app/main.f90": ["linalg"]}
        rel_paths = {"app/main.f90"}

        assert resolve_imports(imports, rel_paths, fortran_modules={}) == []

    def test_include_resolves_beside_the_importer(self):
        imports = {"src/main.f90": ["./consts.inc"]}
        rel_paths = {"src/main.f90", "src/consts.inc"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("src/consts.inc", "EXTRACTED")
        ]


class TestPascalImports:
    """A unit `Foo.Bar` is the one `Foo.Bar.pas` or `.pp` in the repo,
    compared without case. `uses X in 'path'` is a path beside the
    importing file.
    """

    def test_unit_resolves_to_its_pas_file_without_case(self):
        imports = {"app/Main.dpr": ["Foo.Bar"]}
        rel_paths = {"app/Main.dpr", "lib/foo.bar.pas"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("lib/foo.bar.pas", "INFERRED")
        ]

    def test_pp_file_is_a_unit_too(self):
        imports = {"app/Main.dpr": ["Geometry"]}
        rel_paths = {"app/Main.dpr", "src/Geometry.pp"}

        results = resolve_imports(imports, rel_paths)

        assert [r["target"] for r in results] == ["src/Geometry.pp"]

    def test_unit_with_a_pas_and_a_pp_file_is_no_edge(self):
        imports = {"app/Main.dpr": ["Foo"]}
        rel_paths = {"app/Main.dpr", "a/Foo.pas", "b/Foo.pp"}

        assert resolve_imports(imports, rel_paths) == []

    def test_runtime_unit_is_no_edge(self):
        imports = {"app/Main.dpr": ["SysUtils"]}
        rel_paths = {"app/Main.dpr"}

        assert resolve_imports(imports, rel_paths) == []

    def test_uses_in_path_resolves_beside_the_importer(self):
        imports = {"app/Main.dpr": ["lib/Foo.pas"]}
        rel_paths = {"app/Main.dpr", "app/lib/Foo.pas"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("app/lib/Foo.pas", "EXTRACTED")
        ]


def test_each_contract_fortran_and_pascal_project_indexes_symbols_and_edges(tmp_path):
    """One small project per language, with files that import each
    other. Each function is findable, each import links, and a call
    across a file reaches the graph.
    """
    root = tmp_path / "repo"
    _index_repo(
        root,
        {
            "src/Token.sol": "contract Token {\n    function mint(uint x) public {\n    }\n}\n",
            "src/Vault.sol": (
                'import "./Token.sol";\n'
                "contract Vault is Token {\n"
                "    function deposit(uint x) public {\n"
                "        mint(x);\n"
                "    }\n"
                "}\n"
            ),
            "geo/shapes.f90": (
                "module geometry\ncontains\n  subroutine report(r)\n"
                "    real :: r\n  end subroutine report\nend module geometry\n"
            ),
            "geo/main.f90": "program main\n  use GEOMETRY\n  call report(2.0)\nend program main\n",
            "pas/Shapes.pas": (
                "unit Shapes;\ninterface\nprocedure Draw(n: Integer);\n"
                "implementation\nprocedure Draw(n: Integer);\nbegin\nend;\nend.\n"
            ),
            "pas/App.pas": (
                "unit App;\ninterface\nuses Shapes;\nimplementation\n"
                "procedure Run;\nbegin\n  Draw(1);\nend;\nend.\n"
            ),
        },
    )
    pointer = load_pointer(root)
    assert pointer is not None

    for name, language in [
        ("deposit", "solidity"),
        ("mint", "solidity"),
        ("report", "fortran"),
        ("Draw", "pascal"),
    ]:
        matches = symbol_matches(pointer, name)
        assert matches, f"{name} not found in the index"
        exact = {m["language"] for m in matches if m["name"] == name}
        assert language in exact, matches

    imports = _graph_pairs(root, "imports")
    assert ("src/Vault.sol", "src/Token.sol") in imports, sorted(imports)
    assert ("geo/main.f90", "geo/shapes.f90") in imports, sorted(imports)
    assert ("pas/App.pas", "pas/Shapes.pas") in imports, sorted(imports)
    calls = _graph_pairs(root, "calls")
    assert ("deposit", "mint") in calls, sorted(calls)
    assert ("Run", "Draw") in calls, sorted(calls)


def test_fortran_and_pascal_names_match_without_case(tmp_path):
    """Fortran and Pascal ignore case: `CALL REPORT` calls
    `subroutine report`, `Draw(1)` calls `procedure draw`, and a class
    named `tshape` inherits from `TShape`.
    """
    root = tmp_path / "repo"
    _index_repo(
        root,
        {
            "shapes.f90": (
                "module shapes\ncontains\n  subroutine report(r)\n"
                "    real :: r\n  end subroutine report\nend module shapes\n"
            ),
            "runner.f90": (
                "module runner\n  use Shapes\ncontains\n  subroutine go()\n"
                "    call REPORT(2.0)\n  end subroutine go\nend module runner\n"
            ),
            "Shapes.pas": (
                "unit Shapes;\ninterface\ntype\n  TShape = class\n  end;\n"
                "procedure Draw(n: Integer);\nimplementation\n"
                "procedure draw(n: Integer);\nbegin\nend;\nend.\n"
            ),
            "App.pas": (
                "unit App;\ninterface\nuses Shapes;\ntype\n"
                "  TCircle = class(tshape)\n  end;\nimplementation\n"
                "procedure Run;\nbegin\n  Draw(1);\nend;\nend.\n"
            ),
        },
    )

    assert ("go", "report") in _graph_pairs(root, "calls")
    assert ("Run", "draw") in _graph_pairs(root, "calls")
    assert ("TCircle", "TShape") in _graph_pairs(root, "inherits")


class TestStyleImports:
    """CSS `@import` is relative to the stylesheet, and a bare package
    path is under node_modules/. SCSS partials follow Sass's rules.
    """

    def test_css_import_resolves_beside_the_stylesheet(self):
        imports = {"src/app.css": ["./base.css"]}
        rel_paths = {"src/app.css", "src/base.css"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [("src/base.css", "EXTRACTED")]

    def test_css_bare_package_path_resolves_under_node_modules(self):
        imports = {"src/app.css": ["bootstrap/dist/css/bootstrap.css"]}
        rel_paths = {"src/app.css", "node_modules/bootstrap/dist/css/bootstrap.css"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("node_modules/bootstrap/dist/css/bootstrap.css", "INFERRED")
        ]

    def test_css_missing_import_is_no_edge(self):
        imports = {"src/app.css": ["./missing.css"]}
        rel_paths = {"src/app.css"}

        assert resolve_imports(imports, rel_paths) == []

    @pytest.mark.parametrize(
        "target",
        [
            "src/styles/base/_vars.scss",
            "src/styles/base/vars.scss",
            "src/styles/base/_vars/_index.scss",
            "src/styles/base/vars/_index.scss",
        ],
    )
    def test_scss_module_path_finds_each_partial_form(self, target):
        """`@use "base/vars"` is `_vars.scss`, `vars.scss`, or a folder
        index, beside the importer.
        """
        imports = {"src/styles/main.scss": ["base/vars"]}
        rel_paths = {"src/styles/main.scss", target}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [(target, "EXTRACTED")]

    def test_scss_module_falls_back_to_the_repo_root(self):
        imports = {"src/styles/main.scss": ["tokens/colors"]}
        rel_paths = {"src/styles/main.scss", "tokens/_colors.scss"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("tokens/_colors.scss", "INFERRED")
        ]

    def test_scss_tilde_and_bare_packages_resolve_under_node_modules(self):
        imports = {"src/main.scss": ["~bootstrap/scss/functions", "bootstrap/scss/mixins"]}
        rel_paths = {
            "src/main.scss",
            "node_modules/bootstrap/scss/_functions.scss",
            "node_modules/bootstrap/scss/_mixins.scss",
        }

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("node_modules/bootstrap/scss/_functions.scss", "INFERRED"),
            ("node_modules/bootstrap/scss/_mixins.scss", "INFERRED"),
        ]

    def test_scss_dot_relative_path_does_not_reach_the_root(self):
        """`./vars` names a file beside the importer. A file of that
        name at the root is not the same module.
        """
        imports = {"src/styles/main.scss": ["./vars"]}
        rel_paths = {"src/styles/main.scss", "vars.scss"}

        assert resolve_imports(imports, rel_paths) == []

    def test_scss_built_in_and_missing_modules_are_no_edge(self):
        imports = {"src/main.scss": ["sass:math", "./nope"]}
        rel_paths = {"src/main.scss"}

        assert resolve_imports(imports, rel_paths) == []


class TestHtmlAssetImports:
    """A page's local stylesheet or script is relative to the page. A
    path from `/` is the repo root, then public/ and static/.
    """

    def test_page_asset_resolves_beside_the_page(self):
        imports = {"public/index.html": ["css/main.css"]}
        rel_paths = {"public/index.html", "public/css/main.css"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("public/css/main.css", "EXTRACTED")
        ]

    def test_rooted_asset_resolves_from_the_repo_root(self):
        imports = {"public/index.html": ["/js/app.js"]}
        rel_paths = {"public/index.html", "js/app.js"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [("js/app.js", "EXTRACTED")]

    @pytest.mark.parametrize("folder", ["public", "static"])
    def test_rooted_asset_falls_back_to_public_then_static(self, folder):
        imports = {"index.html": ["/css/site.css"]}
        rel_paths = {"index.html", f"{folder}/css/site.css"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [
            (f"{folder}/css/site.css", "INFERRED")
        ]

    def test_missing_rooted_asset_is_no_edge(self):
        imports = {"index.html": ["/missing.js"]}
        rel_paths = {"index.html"}

        assert resolve_imports(imports, rel_paths) == []


class TestVueAndSvelteImports:
    """Vue and Svelte files import like TypeScript, so a component is
    found with or without its extension.
    """

    def test_typescript_import_finds_a_vue_component_without_extension(self):
        imports = {"src/main.ts": ["./components/UserCard"]}
        rel_paths = {"src/main.ts", "src/components/UserCard.vue"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("src/components/UserCard.vue", "EXTRACTED")
        ]

    def test_component_imports_a_svelte_file_and_a_typescript_module(self):
        imports = {"src/App.vue": ["./Counter.svelte", "../api"]}
        rel_paths = {"src/App.vue", "src/Counter.svelte", "api.ts"}

        results = resolve_imports(imports, rel_paths)

        assert [(r["target"], r["confidence"]) for r in results] == [
            ("api.ts", "EXTRACTED"),
            ("src/Counter.svelte", "EXTRACTED"),
        ]

    def test_missing_component_is_no_edge(self):
        imports = {"src/App.vue": ["./Nope"]}
        rel_paths = {"src/App.vue"}

        assert resolve_imports(imports, rel_paths) == []


def test_vue_component_and_its_style_link_into_the_graph(tmp_path):
    """A Vue file imports its component and a TypeScript module, and a
    function in the script calls the module's function across files.
    """
    root = tmp_path / "repo"
    _index_repo(
        root,
        {
            "src/App.vue": (
                '<script setup lang="ts">\n'
                "import UserCard from './components/UserCard.vue'\n"
                "</script>\n"
                "<template><UserCard /></template>\n"
            ),
            "src/components/UserCard.vue": (
                '<script setup lang="ts">\n'
                "import { fetchUser } from '../api'\n"
                "function load() { return fetchUser(1) }\n"
                "</script>\n"
                '<style scoped lang="scss">\n'
                ".card { color: red; }\n"
                "</style>\n"
            ),
            "src/api.ts": "export function fetchUser(id: number) {\n  return id\n}\n",
        },
    )

    assert ("load", "fetchUser") in _graph_pairs(root, "calls")


def test_vue_component_does_not_hide_its_script_functions():
    """A Vue component spans its file, so `impact` still names the
    functions in its script. A JSX component still holds its locals.
    """
    from mimry.queries import _targetable

    vue = [
        {
            "name": "UserCard",
            "kind": "component",
            "language": "vue",
            "line_start": 1,
            "line_end": 9,
        },
        {"name": "load", "kind": "function", "language": "vue", "line_start": 4, "line_end": 6},
    ]
    assert [sym["name"] for sym in _targetable(vue)] == ["UserCard", "load"]
    jsx = [
        {"name": "Card", "kind": "component", "language": "tsx", "line_start": 1, "line_end": 9},
        {"name": "helper", "kind": "function", "language": "tsx", "line_start": 3, "line_end": 4},
    ]
    assert [sym["name"] for sym in _targetable(jsx)] == ["Card"]
