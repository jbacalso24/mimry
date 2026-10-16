from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mimry.commands import cmd_init
from mimry.indexer import write_index
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


def _index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, str]):
    root, pointer = _init_repo(tmp_path, monkeypatch, files)
    write_index(root, pointer)
    index = Path(load_pointer(root)["indexPath"])
    symbols = [
        json.loads(line) for line in (index / "symbols.jsonl").read_text().splitlines() if line
    ]
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
    """A method that calls an imported function produces a
    method->function call edge attributed to the method, not swallowed
    by the enclosing class."""
    symbols, graph = _index(
        tmp_path,
        monkeypatch,
        {
            "src/util.ts": (
                "export function greet(name: string): string { return 'Hi ' + name; }\n"
            ),
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
    """A CommonJS require() import lets a call resolve across files the
    same way an ES import does."""
    symbols, graph = _index(
        tmp_path,
        monkeypatch,
        {
            "src/util.js": (
                "function greet(name) { return 'Hi ' + name; }\nmodule.exports = { greet };\n"
            ),
            "src/legacy.js": (
                "const { greet } = require('./util');\n"
                "function announce(name) { return greet(name); }\n"
                "module.exports = { announce };\n"
            ),
        },
    )
    assert "greet" in _call_targets(graph, symbols, "announce"), (
        "announce() should call greet() via require()"
    )


def _callers_of(graph: dict, symbols: list[dict], callee_name: str) -> set[str]:
    """Names of symbols with a calls edge to callee_name."""
    id_to_name = {s["symbol_id"]: s["name"] for s in symbols}
    callee_ids = {s["symbol_id"] for s in symbols if s["name"] == callee_name}
    return {
        id_to_name.get(e["source"].removeprefix("symbol:"))
        for e in graph["edges"]
        if e.get("relation") == "calls" and e["target"].removeprefix("symbol:") in callee_ids
    }


def test_ts_call_in_a_local_initializer_is_credited_to_the_function(tmp_path, monkeypatch):
    """A call in `const order = insert(path)` inside add is add's call,
    and a plain arrow const is a function whose local calls it owns."""
    symbols, graph = _index(
        tmp_path,
        monkeypatch,
        {
            "src/node.ts": (
                "export function insert(path: string) { return path.length }\n"
                "export function add(path: string) {\n"
                "  const order = insert(path)\n"
                "  return order + 1\n"
                "}\n"
                "export const handler = (x: number) => {\n"
                "  let y = add(String(x))\n"
                "  return y\n"
                "}\n"
            ),
        },
    )

    assert _callers_of(graph, symbols, "insert") == {"add"}
    assert _callers_of(graph, symbols, "add") == {"handler"}
    assert {s["name"]: s["kind"] for s in symbols}["handler"] == "function"


def test_ts_module_level_const_call_still_credits_the_const(tmp_path, monkeypatch):
    symbols, graph = _index(
        tmp_path,
        monkeypatch,
        {"src/config.ts": "export function make() { return 1 }\nexport const x = make()\n"},
    )

    assert _callers_of(graph, symbols, "make") == {"x"}


def test_ts_declarator_kind_follows_its_initializer(tmp_path, monkeypatch):
    """An arrow with JSX is a component, an arrow or function expression
    without it is a function, and any other value is a variable. A
    function wrapped in parentheses, `as`, or `memo`/`forwardRef`/
    `useCallback` keeps its kind; another call's result is a variable.
    """
    symbols, _ = _index(
        tmp_path,
        monkeypatch,
        {
            "src/kinds.tsx": (
                "export const Card = () => <div/>\n"
                "export const n = a < b ? 1 : 2\n"
                "export const f = function () { return 1 }\n"
                "export const g = (x: number) => x + 1\n"
                "export const Memo = React.memo(() => <p/>)\n"
                "export const Ref = forwardRef((props, ref) => <input ref={ref}/>)\n"
                "export const onSave = useCallback(() => save(1), [])\n"
                "export const h = ((x: number) => x) as Fn\n"
                "export const doubled = items.map((i) => i * 2)\n"
                "export const { first, rest = make() } = obj\n"
            ),
        },
    )

    kinds = {s["name"]: s["kind"] for s in symbols}
    # A destructuring pattern declares no single name.
    assert not {"first", "rest", "make", "obj"} & set(kinds)
    assert not [name for name in kinds if "{" in name]
    expected = {
        "Card": "component",
        "n": "variable",
        "f": "function",
        "g": "function",
        "Memo": "component",
        "Ref": "component",
        "onSave": "function",
        "h": "function",
        "doubled": "variable",
    }
    assert {name: kinds[name] for name in expected} == expected
