from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mimry.commands import cmd_init
from mimry.indexer import write_index
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


def _index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, str]):
    root, pointer = _init_repo(tmp_path, monkeypatch, files)
    write_index(root, pointer)
    index = Path(load_pointer(root)["indexPath"])
    symbols = [json.loads(line) for line in (index / "symbols.jsonl").read_text().splitlines() if line]
    graph = json.loads((index / "graph.json").read_text(encoding="utf-8"))
    return symbols, graph


def _call_targets(graph: dict, symbols: list[dict], caller_name: str) -> set[str]:
    """Names of symbols called by the symbol named caller_name."""
    id_to_name = {s["symbol_id"]: s["name"] for s in symbols}
    caller_ids = {s["symbol_id"] for s in symbols if s["name"] == caller_name}
    targets = set()
    for e in graph["edges"]:
        if e.get("relation") == "calls" and e["source"].removeprefix("symbol:") in caller_ids:
            targets.add(id_to_name.get(e["target"].removeprefix("symbol:")))
    return targets


def test_ts_class_method_is_a_symbol_and_owns_its_calls(tmp_path, monkeypatch):
    """A method that calls an imported function produces a method->function call edge
    attributed to the method, not swallowed by the enclosing class."""
    symbols, graph = _index(
        tmp_path,
        monkeypatch,
        {
            "src/util.ts": ("export function greet(name: string): string { return 'Hi ' + name; }\n"),
            "src/service.ts": (
                "import { greet } from './util';\n"
                "export class Greeter {\n"
                "  build(name: string): string {\n"
                "    return greet(name);\n"
                "  }\n"
                "}\n"
            ),
        },
    )
    kinds = {(s["name"], s["kind"]) for s in symbols}
    assert ("build", "method") in kinds, f"method not extracted; symbols={kinds}"
    assert "greet" in _call_targets(graph, symbols, "build"), "build() should call greet()"


def test_js_require_import_resolves_cross_file_call(tmp_path, monkeypatch):
    """A CommonJS require() import lets a call resolve across files the same way an
    ES import does."""
    symbols, graph = _index(
        tmp_path,
        monkeypatch,
        {
            "src/util.js": "function greet(name) { return 'Hi ' + name; }\nmodule.exports = { greet };\n",
            "src/legacy.js": (
                "const { greet } = require('./util');\n"
                "function announce(name) { return greet(name); }\n"
                "module.exports = { announce };\n"
            ),
        },
    )
    assert "greet" in _call_targets(graph, symbols, "announce"), "announce() should call greet() via require()"
