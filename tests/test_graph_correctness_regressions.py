from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mimry.commands import cmd_init
from mimry.freshness import index_freshness
from mimry.indexer import write_index
from mimry.mcp_server import mimry_reindex
from mimry.scanner import adapt
from mimry.state import StateCorruptionError, UNINDEXABLE_FILE
from mimry.storage import load_pointer


def _init_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, str]) -> tuple[Path, dict]:
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


def test_duplicate_method_names_keep_each_call_edge_on_its_real_caller(tmp_path: Path, monkeypatch) -> None:
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
    symbols = [json.loads(line) for line in (index / "symbols.jsonl").read_text().splitlines() if line]
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


def test_python_parent_relative_import_resolves_from_declared_level(tmp_path: Path, monkeypatch) -> None:
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
    assert adapted[3] == ["..util"]

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


def test_write_index_and_reindex_report_enriched_graph_edge_count(tmp_path: Path, monkeypatch) -> None:
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
    reindexed_graph = json.loads((Path(reindex_stats["index"]) / "graph.json").read_text(encoding="utf-8"))
    assert reindex_stats["edges"] == len(reindexed_graph["edges"])
