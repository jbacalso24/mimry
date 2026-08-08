"""Class hierarchy is a first-class relationship, especially in .NET.

53% of the type declarations in a real 955-file C# solution declare a base type or
interface. Without these edges the graph cannot answer "what implements IFoo", which
is the question DI-by-convention registration is built on.
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
        (".cs", "interface IFoo {} class Bar : BaseThing, IFoo { }", {("Bar", "BaseThing"), ("Bar", "IFoo")}),
        (
            ".ts",
            "class Bar extends BaseThing implements IFoo, IBaz {}",
            {("Bar", "BaseThing"), ("Bar", "IFoo"), ("Bar", "IBaz")},
        ),
        (".py", "class Bar(BaseThing, Mixin):\n    pass\n", {("Bar", "BaseThing"), ("Bar", "Mixin")}),
        # The type argument is not a base type.
        (".cs", "class H : IHandler<MyCommand> { }", {("H", "IHandler")}),
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
    """Same refusal to guess as calls: an unresolvable base emits nothing."""
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

    graph = json.loads((root / ".mimry" / "mimry-out" / "graph" / "graph.json").read_text(encoding="utf-8"))
    labels = {n["id"]: n.get("label") for n in graph["nodes"]}
    pairs = {
        (labels.get(e["source"]), labels.get(e["target"])) for e in graph["edges"] if e.get("relation") == "inherits"
    }

    assert ("Handler", "IHandler") in pairs, sorted(p for p in pairs if p[0])


def test_base_type_resolves_repo_wide_when_unique() -> None:
    """C# reaches a base through `using`, not a path import, so path resolution fails.

    A name owned by exactly one file in the repo is determined, not guessed.
    """
    inherits = {"app/handler.cs": [{"type": "Handler", "base": "IHandler", "line": 1}]}
    symbols = {
        "app/handler.cs": [{"name": "Handler", "kind": "class", "line_start": 1, "line_end": 2}],
        "app/contracts.cs": [{"name": "IHandler", "kind": "interface", "line_start": 1, "line_end": 2}],
    }

    edges = resolve_inheritance(inherits, symbols, [])

    assert len(edges) == 1
    assert edges[0]["base_file"] == "app/contracts.cs"
    assert edges[0]["confidence"] == "INFERRED"


def test_ambiguous_base_name_still_declines() -> None:
    """Two files own the name -> emit nothing, same contract as calls."""
    inherits = {"app/handler.cs": [{"type": "Handler", "base": "IHandler", "line": 1}]}
    symbols = {
        "app/handler.cs": [{"name": "Handler", "kind": "class", "line_start": 1, "line_end": 2}],
        "app/a.cs": [{"name": "IHandler", "kind": "interface", "line_start": 1, "line_end": 2}],
        "app/b.cs": [{"name": "IHandler", "kind": "interface", "line_start": 1, "line_end": 2}],
    }

    assert resolve_inheritance(inherits, symbols, []) == []
