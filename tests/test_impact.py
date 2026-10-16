from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from mimry import mcp_tools

ROOT = Path(__file__).resolve().parents[1]

CHAIN = """def a():
    return 1


def b():
    return a()


def c():
    return b()
"""

# Imports a, calls nothing in it.
IMPORTER = """from app.chain import a


def uses_import():
    return 2
"""

TEST_CHAIN = """from app.chain import b


def test_b():
    assert b() == 1
"""

LONELY = """def solo():
    return 3
"""

PYPROJECT = """[project]
name = "fixture"
version = "0.1"
dependencies = []

[tool.pytest.ini_options]

[dependency-groups]
dev = ["pytest"]
"""

FILES = {
    "app/chain.py": CHAIN,
    "app/importer.py": IMPORTER,
    "app/lonely.py": LONELY,
    "tests/test_chain.py": TEST_CHAIN,
    "pyproject.toml": PYPROJECT,
}

# app/two.py reaches helper through the package __init__, a re-export.
REEXPORT = {
    "app/__init__.py": "from app.one import helper\n",
    "app/one.py": "def helper(x):\n    return x\n\n\ndef main():\n    return helper(1)\n",
    "app/two.py": "from app import helper\n\n\ndef helper2():\n    return helper(2)\n",
}

# test_util imports util. test_service reaches it only via run(), which
# calls used(), so the symbol-level answer misses it.
UTIL_FILES = {
    "app/util.py": "def used():\n    return 1\n\n\ndef other():\n    return 2\n",
    "app/service.py": "from app.util import used\n\n\ndef run():\n    return used()\n",
    "tests/test_service.py": (
        "from app.service import run\n\n\ndef test_run():\n    assert run() == 1\n"
    ),
    "tests/test_util.py": (
        "from app.util import used\n\n\ndef test_used():\n    assert used() == 1\n"
    ),
    "pyproject.toml": PYPROJECT,
}


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _mimry(cwd: Path, cache: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["MIMRY_CACHE_HOME"] = str(cache)
    return subprocess.run(
        [sys.executable, "-m", "mimry.cli", *args],
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def _repo(tmp_path: Path, files: dict[str, str]) -> tuple[Path, Path]:
    cache = tmp_path / "cache"
    repo = tmp_path / "repo"
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "MIMRY Test")
    _git(repo, "config", "user.email", "test@example.com")
    assert _mimry(repo, cache, "init").returncode == 0
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Initial commit")
    assert _mimry(repo, cache, "index").returncode == 0
    return repo, cache


def _json(repo: Path, cache: Path, *args: str) -> dict:
    res = _mimry(repo, cache, "impact", *args, "--json")
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def _depths(payload: dict) -> dict[str, int]:
    return {row["name"]: row["depth"] for row in payload["callers"]}


def test_callers_are_grouped_by_depth_and_depth_one_stops_early(tmp_path):
    repo, cache = _repo(tmp_path, FILES)

    deep = _json(repo, cache, "a")
    shallow = _json(repo, cache, "a", "--depth", "1")

    assert _depths(deep)["b"] == 1
    assert _depths(deep)["c"] == 2
    assert set(_depths(shallow)) == {"b"}

    res = _mimry(repo, cache, "impact", "a")
    assert res.returncode == 0, res.stderr
    assert res.stdout.index("Direct callers") < res.stdout.index("Callers 2 calls away")
    assert "a: 1 direct source caller, 2 source callers within 3 calls" in res.stdout


def test_test_caller_is_hidden_in_text_but_kept_in_json(tmp_path):
    repo, cache = _repo(tmp_path, FILES)

    text = _mimry(repo, cache, "impact", "a").stdout
    callers = {row["name"]: row for row in _json(repo, cache, "a")["callers"]}

    assert "Callers 2 calls away (1, +1 in tests)" in text
    assert "test_b (tests/" not in text
    assert callers["test_b"]["test"] is True
    assert callers["c"]["test"] is False


def test_changed_markdown_file_adds_no_heading_targets(tmp_path):
    readme = "# Title\n\n## Usage\n\nText.\n"
    repo, cache = _repo(tmp_path, {**FILES, "README.md": readme})
    (repo / "README.md").write_text(readme.replace("Text.", "More text."), encoding="utf-8")

    changed = _json(repo, cache, "--changed")
    by_file = _json(repo, cache, "README.md")

    assert changed["targets"] == []
    assert by_file["targets"] == []
    assert changed["summary"].startswith("The working tree: 0 direct source callers")


def test_importer_that_calls_nothing_is_listed_under_importers(tmp_path):
    repo, cache = _repo(tmp_path, FILES)

    payload = _json(repo, cache, "a")

    assert "app/importer.py" in payload["importers"]
    assert "app/importer.py" not in {row["path"] for row in payload["callers"]}


def test_test_file_reaching_the_target_is_listed_with_the_pytest_command(tmp_path):
    repo, cache = _repo(tmp_path, FILES)

    payload = _json(repo, cache, "b")
    text = _mimry(repo, cache, "impact", "b").stdout

    assert payload["test_command"] == "uv run pytest"
    assert payload["tests"] == ["tests/test_chain.py"]
    assert "uv run pytest tests/test_chain.py" in text


def test_change_that_no_test_reaches_says_so(tmp_path):
    repo, cache = _repo(tmp_path, FILES)

    text = _mimry(repo, cache, "impact", "solo").stdout

    assert _json(repo, cache, "solo")["tests"] == []
    assert "No tests reach these changes." in text


def test_file_target_covers_every_symbol_in_the_file(tmp_path):
    repo, cache = _repo(tmp_path, FILES)

    payload = _json(repo, cache, "app/chain.py")

    assert {t["name"] for t in payload["targets"]} == {"a", "b", "c"}
    assert "app/importer.py" in payload["importers"]
    assert "tests/test_chain.py" in payload["tests"]


def test_changed_targets_only_the_edited_symbol_and_new_files_count(tmp_path):
    repo, cache = _repo(tmp_path, FILES)
    (repo / "app" / "chain.py").write_text(CHAIN.replace("return 1", "return 2"), encoding="utf-8")
    (repo / "app" / "fresh.py").write_text("def fresh():\n    return 0\n", encoding="utf-8")

    payload = _json(repo, cache, "--changed")

    assert {t["name"] for t in payload["targets"]} == {"a", "fresh"}
    assert payload["summary"].startswith("The working tree: ")


def test_changed_with_a_clean_tree_says_so(tmp_path):
    repo, cache = _repo(tmp_path, FILES)

    res = _mimry(repo, cache, "impact", "--changed")

    assert res.returncode == 0, res.stderr
    assert "No changes in the working tree against HEAD." in res.stdout


def test_inferred_caller_is_marked_and_json_carries_confidence(tmp_path):
    repo, cache = _repo(tmp_path, REEXPORT)

    payload = _json(repo, cache, "helper", "--file", "app/one.py")
    text = _mimry(repo, cache, "impact", "helper", "--file", "app/one.py").stdout

    confidence = {row["name"]: row["confidence"] for row in payload["callers"]}
    assert confidence == {"main": "EXTRACTED", "helper2": "INFERRED"}
    assert "helper2 (app/two.py:4) (inferred)" in text
    assert "main (app/one.py:5)" in text


def test_json_has_the_documented_shape(tmp_path):
    repo, cache = _repo(tmp_path, FILES)

    payload = _json(repo, cache, "a")

    assert set(payload) == {
        "targets",
        "callers",
        "importers",
        "tests",
        "fallback",
        "test_command",
        "summary",
    }
    assert all(
        set(row) == {"name", "kind", "path", "line", "depth", "confidence", "test"}
        for row in payload["callers"]
    )


def test_unknown_symbol_suggests_similar_names(tmp_path):
    repo, cache = _repo(tmp_path, FILES)

    res = _mimry(repo, cache, "impact", "sol")

    assert res.returncode == 1, res.stderr
    assert "Did you mean:" in res.stdout
    assert "solo" in res.stdout


def test_impact_of_a_name_or_file_missing_from_the_graph_exits_1(tmp_path):
    repo, cache = _repo(tmp_path, FILES)

    res = _mimry(repo, cache, "impact", "nope_missing")
    as_json = _mimry(repo, cache, "impact", "app/nope.py", "--json")

    assert res.returncode == 1, res.stderr
    assert "No function, method or file" in res.stdout
    assert as_json.returncode == 1, as_json.stderr
    assert json.loads(as_json.stdout)["error"]["code"] == "unknown_symbol"


def test_depth_outside_one_to_five_is_rejected(tmp_path):
    repo, cache = _repo(tmp_path, FILES)

    res = _mimry(repo, cache, "impact", "a", "--depth", "6")

    assert res.returncode == 2
    assert "invalid choice" in res.stderr


def test_mcp_impact_returns_the_same_payload_as_json(tmp_path, monkeypatch):
    repo, cache = _repo(tmp_path, FILES)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))

    via_mcp = mcp_tools.mimry_impact("a", root=str(repo))

    assert via_mcp == _json(repo, cache, "a")


def test_mcp_impact_rejects_a_depth_outside_one_to_five(tmp_path, monkeypatch):
    repo, cache = _repo(tmp_path, FILES)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))

    payload = mcp_tools.mimry_impact("a", root=str(repo), depth=6)

    assert payload["returncode"] == 2
    assert payload["error"]["code"] == "invalid_depth"


def test_uncalled_new_symbol_falls_back_to_the_file_tests(tmp_path):
    repo, cache = _repo(tmp_path, UTIL_FILES)
    (repo / "app" / "util.py").write_text(
        UTIL_FILES["app/util.py"] + "\n\ndef fresh():\n    return 3\n", encoding="utf-8"
    )

    payload = _json(repo, cache, "--changed")
    text = _mimry(repo, cache, "impact", "--changed").stdout

    assert {t["name"] for t in payload["targets"]} == {"fresh"}
    assert payload["fallback"] == ["app/util.py"]
    assert payload["tests"] == ["tests/test_service.py", "tests/test_util.py"]
    assert payload["tests"] == _json(repo, cache, "app/util.py")["tests"]
    assert (
        "No callers for the changed symbols in app/util.py, "
        "so these are the tests that reach the file." in text
    )
    assert "uv run pytest tests/test_service.py tests/test_util.py" in text


def test_changed_symbol_with_callers_does_not_fall_back(tmp_path):
    repo, cache = _repo(tmp_path, UTIL_FILES)
    (repo / "app" / "util.py").write_text(
        UTIL_FILES["app/util.py"].replace("return 1", "return 10"), encoding="utf-8"
    )

    payload = _json(repo, cache, "--changed")
    text = _mimry(repo, cache, "impact", "--changed").stdout

    assert {t["name"] for t in payload["targets"]} == {"used"}
    assert payload["fallback"] == []
    assert "No callers for the changed symbols" not in text


# A function holds the locals curNode and i; a class holds a method with
# the local order. emptyParams is a module-level constant.
TS_NODE = """export const emptyParams: Record<string, string> = {}

export function insert(path: string): number {
  let curNode = 0
  for (let i = 0; i < path.length; i++) {
    curNode += path.charCodeAt(i)
  }
  return curNode
}

export class Router {
  add(path: string): number {
    const order = insert(path)
    return order
  }
}
"""

# Calls insert from a local, so the call must be credited to register.
TS_CALLER = """import { insert, Router } from "./node"

export function register(path: string): number {
  const r = new Router()
  const total = insert(path)
  return r.add(path) + total
}
"""

# An arrow component with a local, as React files write them.
TS_PANEL = """export const Panel = () => {
  const count = 2
  return <div>{count}</div>
}
"""

PY_BOX = """class Box:
    def size(self):
        total = 0

        def helper():
            return 1

        return total + helper()


def top():
    return 2
"""


def test_ts_targets_skip_locals_but_keep_functions_classes_methods_and_constants(tmp_path):
    repo, cache = _repo(
        tmp_path,
        {
            "src/node.ts": TS_NODE,
            "src/caller.ts": TS_CALLER,
            "src/ui/Panel.tsx": TS_PANEL,
        },
    )

    text = _mimry(repo, cache, "impact", "src/node.ts").stdout
    names = {t["name"] for t in _json(repo, cache, "src/node.ts")["targets"]}
    panel = {t["name"] for t in _json(repo, cache, "src/ui/Panel.tsx")["targets"]}

    assert names == {"emptyParams", "insert", "Router", "add"}
    assert panel == {"Panel"}
    assert "curNode" not in text and "order" not in text
    assert {c["name"] for c in _json(repo, cache, "src/node.ts")["callers"]} == {"register"}


def test_ts_caller_through_a_local_is_credited_to_the_function(tmp_path):
    repo, cache = _repo(tmp_path, {"src/node.ts": TS_NODE, "src/caller.ts": TS_CALLER})

    callers = {c["name"] for c in _json(repo, cache, "insert")["callers"]}

    assert callers == {"add", "register"}


def test_changed_inside_a_function_body_targets_the_function_only(tmp_path):
    repo, cache = _repo(tmp_path, {"src/node.ts": TS_NODE, "src/caller.ts": TS_CALLER})
    edited = TS_NODE.replace("let curNode = 0", "let curNode = 1")
    (repo / "src" / "node.ts").write_text(edited, encoding="utf-8")

    payload = _json(repo, cache, "--changed")

    assert {t["name"] for t in payload["targets"]} == {"insert"}


def test_python_nested_def_is_not_a_target_but_methods_are(tmp_path):
    repo, cache = _repo(tmp_path, {"app/box.py": PY_BOX})

    names = {t["name"] for t in _json(repo, cache, "app/box.py")["targets"]}

    assert names == {"Box", "size", "top"}
