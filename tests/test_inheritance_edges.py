"""Class hierarchy is a first-class relationship, especially in .NET.

53% of the type declarations in a real 955-file C# solution declare a
base type or interface. Without these edges the graph cannot answer
"what implements IFoo", which is the question DI-by-convention
registration is built on.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mimry.commands import cmd_init
from mimry.core.languages import extract
from mimry.core.resolve import resolve_inheritance
from mimry.indexer import write_index
from mimry.storage import load_pointer


@pytest.mark.parametrize(
    "suffix,source,expected",
    [
        (
            ".cs",
            "interface IFoo {} class Bar : BaseThing, IFoo { }",
            {("Bar", "BaseThing"), ("Bar", "IFoo")},
        ),
        (
            ".ts",
            "class Bar extends BaseThing implements IFoo, IBaz {}",
            {("Bar", "BaseThing"), ("Bar", "IFoo"), ("Bar", "IBaz")},
        ),
        (
            ".py",
            "class Bar(BaseThing, Mixin):\n    pass\n",
            {("Bar", "BaseThing"), ("Bar", "Mixin")},
        ),
        # The type argument is not a base type.
        (".cs", "class H : IHandler<MyCommand> { }", {("H", "IHandler")}),
        (
            ".dart",
            "class Bar extends BaseThing with Logging implements IFoo {}",
            {("Bar", "BaseThing"), ("Bar", "Logging"), ("Bar", "IFoo")},
        ),
        # Generic arguments are sibling nodes in Dart, so Base<T> names
        # Base.
        (".dart", "class Bar extends Base<Thing> {}", {("Bar", "Base")}),
        (
            ".m",
            "@interface Widget : UIView <Drawable, NSCopying>\n@end\n",
            {("Widget", "UIView"), ("Widget", "Drawable"), ("Widget", "NSCopying")},
        ),
    ],
)
def test_base_types_are_extracted(tmp_path: Path, suffix: str, source: str, expected: set) -> None:
    path = tmp_path / f"types{suffix}"
    path.write_text(source, encoding="utf-8")

    got = {(i["type"], i["base"]) for i in extract(path, source)["inherits"]}

    assert expected <= got, f"missing {expected - got}; got {sorted(got)}"


def test_interfaces_are_indexed_as_symbols(tmp_path: Path) -> None:
    """An interface must exist as a node or nothing can point at it."""
    path = tmp_path / "IFoo.cs"
    source = "namespace N { public interface IFoo { void Go(); } }"
    path.write_text(source, encoding="utf-8")

    defs = {d["name"]: d["kind"] for d in extract(path, source)["definitions"]}

    assert defs.get("IFoo") == "interface", defs


def test_resolution_declines_when_the_base_is_unknown() -> None:
    """Like calls, an unresolvable base emits nothing; no guessing."""
    inherits = {"a.cs": [{"type": "Bar", "base": "SomethingExternal", "line": 1}]}
    symbols = {"a.cs": [{"name": "Bar", "kind": "class", "line_start": 1, "line_end": 2}]}

    assert resolve_inheritance(inherits, symbols, []) == []


def test_inherits_edges_reach_the_published_graph(tmp_path: Path) -> None:
    """End to end: extraction -> resolution -> graph.json."""
    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True)
    (root / "app" / "types.cs").write_text(
        "namespace N { public interface IHandler { } public class Handler : IHandler { } }",
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
    pairs = {
        (labels.get(e["source"]), labels.get(e["target"]))
        for e in graph["edges"]
        if e.get("relation") == "inherits"
    }

    assert ("Handler", "IHandler") in pairs, sorted(p for p in pairs if p[0])


def test_base_type_resolves_repo_wide_when_unique() -> None:
    """C# reaches a base through `using`, not a path import, so path
    resolution fails.

    A name owned by exactly one file in the repo is determined, not
    guessed.
    """
    inherits = {"app/handler.cs": [{"type": "Handler", "base": "IHandler", "line": 1}]}
    symbols = {
        "app/handler.cs": [{"name": "Handler", "kind": "class", "line_start": 1, "line_end": 2}],
        "app/contracts.cs": [
            {"name": "IHandler", "kind": "interface", "line_start": 1, "line_end": 2}
        ],
    }

    edges = resolve_inheritance(inherits, symbols, [])

    assert len(edges) == 1
    assert edges[0]["base_file"] == "app/contracts.cs"
    assert edges[0]["confidence"] == "INFERRED"


def test_ambiguous_base_name_still_declines() -> None:
    """Two files own the name -> emit nothing, as calls do."""
    inherits = {"app/handler.cs": [{"type": "Handler", "base": "IHandler", "line": 1}]}
    symbols = {
        "app/handler.cs": [{"name": "Handler", "kind": "class", "line_start": 1, "line_end": 2}],
        "app/a.cs": [{"name": "IHandler", "kind": "interface", "line_start": 1, "line_end": 2}],
        "app/b.cs": [{"name": "IHandler", "kind": "interface", "line_start": 1, "line_end": 2}],
    }

    assert resolve_inheritance(inherits, symbols, []) == []


def test_python_from_dot_import_reaches_graph(tmp_path: Path) -> None:
    """Python from . import is indexed as a relative import edge."""
    root = tmp_path / "repo"
    pkg = root / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "ui.py").write_text("class UIWidget: pass", encoding="utf-8")
    (pkg / "cli.py").write_text("from . import ui", encoding="utf-8")

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
        if e.get("relation") == "imports"
    }

    assert ("pkg/cli.py", "pkg/ui.py") in imports, (
        f"Python from . import edge missing; got imports: {sorted(p for p in imports if p[0])}"
    )


def test_perl_use_parent_resolves_to_the_unique_package() -> None:
    """A Perl `use parent` names a package. The repo owns it once, so
    the base resolves even though `use parent` is not an import.
    """
    inherits = {"lib/Acme/Widget.pm": [{"type": "Acme::Widget", "base": "Acme::Base", "line": 2}]}
    symbols = {
        "lib/Acme/Widget.pm": [
            {"name": "Acme::Widget", "kind": "module", "language": "perl", "line_start": 1}
        ],
        "lib/Acme/Base.pm": [
            {"name": "Acme::Base", "kind": "module", "language": "perl", "line_start": 1}
        ],
    }

    edges = resolve_inheritance(inherits, symbols, [])

    assert [(e["base_file"], e["base_symbol"], e["confidence"]) for e in edges] == [
        ("lib/Acme/Base.pm", "Acme::Base", "INFERRED")
    ]


def test_namespace_module_does_not_own_a_base_name() -> None:
    """A non-Perl module symbol is a namespace, not a type, so it cannot
    be the base a class names.
    """
    inherits = {"app/handler.cs": [{"type": "Handler", "base": "Shared", "line": 1}]}
    symbols = {
        "app/handler.cs": [{"name": "Handler", "kind": "class", "line_start": 1, "line_end": 2}],
        "app/shared.cs": [
            {"name": "Shared", "kind": "module", "language": "csharp", "line_start": 1}
        ],
    }

    assert resolve_inheritance(inherits, symbols, []) == []


_OBJC_PAIR = {
    "A.m": '#import "B.h"\n\n@interface A : B\n@end\n\n@implementation A\n@end\n',
    "B.h": "#import <Foundation/Foundation.h>\n\n@interface B : NSObject\n@end\n",
}


def test_objc_header_superclass_reaches_the_graph(tmp_path: Path) -> None:
    """A `.h` with @interface is parsed as Objective-C, so the class it
    declares can be the base of a class in a `.m` file.
    """
    root = tmp_path / "repo"
    root.mkdir()
    for rel, text in _OBJC_PAIR.items():
        (root / rel).write_text(text, encoding="utf-8")

    assert cmd_init(SimpleNamespace(root=root, root_type="repo", skip_graph=True)) == 0
    pointer = load_pointer(root)
    assert pointer is not None
    write_index(root, pointer)

    graph = json.loads(
        (root / ".mimry" / "mimry-out" / "graph" / "graph.json").read_text(encoding="utf-8")
    )
    labels = {n["id"]: n.get("label") for n in graph["nodes"]}
    pairs = {
        (labels.get(e["source"]), labels.get(e["target"]))
        for e in graph["edges"]
        if e.get("relation") == "inherits"
    }
    assert ("A", "B") in pairs, sorted(p for p in pairs if p[0])


def test_h_language_override_keeps_the_header_as_c(tmp_path: Path) -> None:
    """A mimry.toml override of `.h` wins over the content check."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "mimry.toml").write_text('[index.languages]\n".h" = "c"\n', encoding="utf-8")
    for rel, text in _OBJC_PAIR.items():
        (root / rel).write_text(text, encoding="utf-8")

    assert cmd_init(SimpleNamespace(root=root, root_type="repo", skip_graph=True)) == 0
    pointer = load_pointer(root)
    assert pointer is not None
    write_index(root, pointer)

    graph = json.loads(
        (root / ".mimry" / "mimry-out" / "graph" / "graph.json").read_text(encoding="utf-8")
    )
    labels = {n["id"]: n.get("label") for n in graph["nodes"]}
    pairs = {
        (labels.get(e["source"]), labels.get(e["target"]))
        for e in graph["edges"]
        if e.get("relation") == "inherits"
    }
    assert ("A", "B") not in pairs, sorted(p for p in pairs if p[0])
