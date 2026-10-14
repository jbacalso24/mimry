from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mimry.commands import cmd_init
from mimry.freshness import index_freshness
from mimry.indexer import write_index
from mimry.mcp_tools import mimry_reindex
from mimry.scanner import adapt
from mimry.state import UNINDEXABLE_FILE, StateCorruptionError
from mimry.storage import load_pointer


def _init_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, str]
) -> tuple[Path, dict]:
    root = tmp_path / "repo"
    for rel_path, source in files.items():
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    assert cmd_init(SimpleNamespace(root=root, root_type="repo", skip_graph=True)) == 0
    pointer = load_pointer(root)
    assert pointer is not None
    return root, pointer


def test_duplicate_method_names_keep_each_call_edge_on_its_real_caller(
    tmp_path: Path, monkeypatch
) -> None:
    root, pointer = _init_repo(
        tmp_path,
        monkeypatch,
        {
            "service.py": """
def helper():
    return None

class First:
    def run(self):
        helper()

class Second:
    def run(self):
        helper()
""",
        },
    )

    write_index(root, pointer)
    published = load_pointer(root)
    assert published is not None
    index = Path(published["indexPath"])
    symbols = [
        json.loads(line) for line in (index / "symbols.jsonl").read_text().splitlines() if line
    ]
    graph = json.loads((index / "graph.json").read_text(encoding="utf-8"))
    helper_id = next(symbol["symbol_id"] for symbol in symbols if symbol["name"] == "helper")
    run_ids = {symbol["symbol_id"] for symbol in symbols if symbol["name"] == "run"}

    callers = {
        edge["source"].removeprefix("symbol:")
        for edge in graph["edges"]
        if edge["relation"] == "calls" and edge["target"] == f"symbol:{helper_id}"
    }

    assert callers == run_ids
    assert len(callers) == 2


def test_python_parent_relative_import_resolves_from_declared_level(
    tmp_path: Path, monkeypatch
) -> None:
    root, pointer = _init_repo(
        tmp_path,
        monkeypatch,
        {
            "pkg/util.py": "def f():\n    return None\n",
            "pkg/sub/util.py": "def wrong():\n    return None\n",
            "pkg/sub/mod.py": "from ..util import f\n\ndef use():\n    f()\n",
        },
    )

    adapted = adapt(root / "pkg/sub/mod.py", root)
    # `..util.f` is the submodule form of `from ..util import f`; the
    # resolver drops it unless `f` is a file.
    assert adapted[3] == ["..util", "..util.f"]

    write_index(root, pointer)
    published = load_pointer(root)
    assert published is not None
    graph = json.loads((Path(published["indexPath"]) / "graph.json").read_text(encoding="utf-8"))
    labels = {node["id"]: node["label"] for node in graph["nodes"]}
    imported_files = {
        labels[edge["target"]]
        for edge in graph["edges"]
        if edge["relation"] == "imports" and labels[edge["source"]] == "pkg/sub/mod.py"
    }

    assert imported_files == {"pkg/util.py"}


def test_unindexable_sidecar_is_generation_integrity_checked(tmp_path: Path, monkeypatch) -> None:
    root, pointer = _init_repo(tmp_path, monkeypatch, {"app.py": "def go():\n    return 1\n"})
    write_index(root, pointer)
    published = load_pointer(root)
    assert published is not None
    unindexable = Path(published["indexPath"]) / UNINDEXABLE_FILE
    unindexable.write_text('["tampered.py"]\n', encoding="utf-8")

    with pytest.raises(StateCorruptionError, match="checksum does not match generation"):
        index_freshness(root, published)


def test_write_index_and_reindex_report_enriched_graph_edge_count(
    tmp_path: Path, monkeypatch
) -> None:
    root, pointer = _init_repo(
        tmp_path,
        monkeypatch,
        {
            "util.py": "def f():\n    return None\n",
            "main.py": "import util\n\ndef use():\n    f()\n",
        },
    )

    stats = write_index(root, pointer)
    graph = json.loads((Path(stats["index"]) / "graph.json").read_text(encoding="utf-8"))
    assert stats["edges"] == len(graph["edges"])
    assert stats["edges"] > 2  # two definition edges plus resolved import/call edges

    reindex_stats = mimry_reindex(str(root))
    reindexed_graph = json.loads(
        (Path(reindex_stats["index"]) / "graph.json").read_text(encoding="utf-8")
    )
    assert reindex_stats["edges"] == len(reindexed_graph["edges"])


def _call_edges(index: Path) -> set[str]:
    """'file:caller:line -> file:callee:line' for each calls edge."""
    files = {}
    for line in (index / "files.jsonl").read_text(encoding="utf-8").splitlines():
        if line:
            record = json.loads(line)
            files[record["file_id"]] = record["rel_path"]
    described = {}
    for line in (index / "symbols.jsonl").read_text(encoding="utf-8").splitlines():
        if line:
            symbol = json.loads(line)
            described[symbol["symbol_id"]] = (
                f"{files[symbol['file_id']]}:{symbol['name']}:{symbol['line_start']}"
            )
    graph = json.loads((index / "graph.json").read_text(encoding="utf-8"))
    return {
        f"{described[edge['source'].removeprefix('symbol:')]} -> "
        f"{described[edge['target'].removeprefix('symbol:')]}"
        for edge in graph["edges"]
        if edge["relation"] == "calls"
    }


def test_bare_call_prefers_the_imported_function_over_a_same_file_method(
    tmp_path: Path, monkeypatch
) -> None:
    root, pointer = _init_repo(
        tmp_path,
        monkeypatch,
        {
            "store.py": "def save(x):\n    return x\n",
            "thing.py": (
                "from store import save\n\nclass Thing:\n    def save(self, x):\n"
                "        return x\n\ndef a():\n    return save(1)\n"
            ),
        },
    )

    stats = write_index(root, pointer)
    edges = _call_edges(Path(stats["index"]))

    assert "thing.py:a:7 -> store.py:save:1" in edges
    assert not any(edge.endswith("-> thing.py:save:5") for edge in edges)


def test_self_call_prefers_the_method_of_the_enclosing_class(tmp_path: Path, monkeypatch) -> None:
    root, pointer = _init_repo(
        tmp_path,
        monkeypatch,
        {
            "same.py": (
                "def save(x):\n    return x\n\nclass Repo:\n    def save(self, x):\n"
                "        return x\n\n    def put(self, x):\n        return self.save(x)\n"
            ),
        },
    )

    stats = write_index(root, pointer)
    edges = _call_edges(Path(stats["index"]))

    assert "same.py:put:8 -> same.py:save:5" in edges
    assert "same.py:put:8 -> same.py:save:1" not in edges


def test_js_this_call_prefers_the_method_of_the_enclosing_class(
    tmp_path: Path, monkeypatch
) -> None:
    root, pointer = _init_repo(
        tmp_path,
        monkeypatch,
        {
            "repo.js": (
                "function save(x) {\n  return x;\n}\nclass Repo {\n  save(x) {\n"
                "    return x;\n  }\n  put(x) {\n    return this.save(x);\n  }\n}\n"
            ),
        },
    )

    stats = write_index(root, pointer)
    edges = _call_edges(Path(stats["index"]))

    assert "repo.js:put:8 -> repo.js:save:5" in edges
    assert "repo.js:put:8 -> repo.js:save:1" not in edges


def test_call_through_a_package_reexport_reaches_the_defining_file(
    tmp_path: Path, monkeypatch
) -> None:
    root, pointer = _init_repo(
        tmp_path,
        monkeypatch,
        {
            "pkg/__init__.py": "from .core import Engine\n",
            "pkg/core.py": "class Engine:\n    def run(self):\n        return 1\n",
            "app.py": "from pkg import Engine\n\ndef main():\n    Engine().run()\n",
        },
    )

    stats = write_index(root, pointer)
    edges = _call_edges(Path(stats["index"]))

    assert "app.py:main:3 -> pkg/core.py:Engine:1" in edges


def test_python_bare_call_never_reaches_an_unimported_file_by_name(
    tmp_path: Path, monkeypatch
) -> None:
    root, pointer = _init_repo(
        tmp_path,
        monkeypatch,
        {
            "a.py": "def a():\n    return helper()\n",
            "b.py": "def helper():\n    return 1\n",
        },
    )

    stats = write_index(root, pointer)

    assert not any(edge.startswith("a.py:") for edge in _call_edges(Path(stats["index"])))


def test_swift_call_within_one_module_resolves_to_the_unique_owner(
    tmp_path: Path, monkeypatch
) -> None:
    root, pointer = _init_repo(
        tmp_path,
        monkeypatch,
        {
            "A.swift": "class A: B {\n  func run() -> Int { return helper() }\n}\n",
            "B.swift": "class B {}\nfunc helper() -> Int { return 1 }\n",
        },
    )

    stats = write_index(root, pointer)

    assert "A.swift:run:2 -> B.swift:helper:2" in _call_edges(Path(stats["index"]))


def test_swift_call_with_two_owners_of_the_name_stays_unresolved(
    tmp_path: Path, monkeypatch
) -> None:
    root, pointer = _init_repo(
        tmp_path,
        monkeypatch,
        {
            "A.swift": "class A {\n  func run() -> Int { return helper() }\n}\n",
            "B.swift": "func helper() -> Int { return 1 }\n",
            "C.swift": "func helper() -> Int { return 2 }\n",
        },
    )

    stats = write_index(root, pointer)

    assert not any(edge.startswith("A.swift:") for edge in _call_edges(Path(stats["index"])))
