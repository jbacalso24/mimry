"""A file MIMRY refuses to index must not count as a file the user changed.

Found on a real 5,796-file C# solution: one file was refused by the adapter's secret
guard, so it was absent from files.jsonl. Freshness treats "on disk but not indexed"
as changed, so the index reported `stale` immediately after a successful reindex --
permanently. graph_health marks the graph stale whenever index_state is not `current`,
so `mimry path` refused to run at all, and `refresh` / `reindex` bounced the user
between each other with no way out.

The refusal is driven by content heuristics, so these tests inject the refusal
directly rather than depending on which strings happen to trip the scanner.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mimry.commands import cmd_init
from mimry.core.artifacts import graph_health
from mimry.freshness import index_freshness
from mimry.indexer import write_index
from mimry.state import UNINDEXABLE_FILE
from mimry.storage import load_pointer

REFUSED = "app/refused.py"


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True)
    (root / "app" / "clean.py").write_text("def go():\n    return 1\n", encoding="utf-8")
    (root / "app" / "refused.py").write_text("def secret_thing():\n    return 2\n", encoding="utf-8")
    return root


def _index(root: Path) -> dict:
    assert cmd_init(SimpleNamespace(root=root, root_type="repo", skip_graph=True)) == 0
    pointer = load_pointer(root)
    assert pointer is not None
    write_index(root, pointer)
    refreshed = load_pointer(root)
    assert refreshed is not None
    return refreshed


@pytest.fixture
def refuse_one_file(monkeypatch: pytest.MonkeyPatch):
    """Make the adapter refuse exactly one file, the way the secret guard does."""
    import mimry.indexer as indexer

    real_adapt = indexer.adapt

    def guarded(path: Path, root: Path):
        if path.name == "refused.py":
            raise ValueError("adapter output contained sensitive data")
        return real_adapt(path, root)

    monkeypatch.setattr(indexer, "adapt", guarded)


def test_refused_file_does_not_keep_the_index_stale(tmp_path: Path, refuse_one_file) -> None:
    root = _repo(tmp_path)
    ptr = _index(root)

    fresh = index_freshness(root, ptr)

    assert fresh["state"] == "current", f"stale right after indexing; changed={fresh['changed']}"
    assert REFUSED not in fresh["changed"]
    assert fresh["unindexable_count"] == 1

    # The reason this matters: graph commands stay usable.
    assert graph_health(root, index_state=fresh["state"])["status"] != "stale"


def test_refused_file_is_still_kept_out_of_the_index(tmp_path: Path, refuse_one_file) -> None:
    """Recording the path must not smuggle the file's content back in."""
    root = _repo(tmp_path)
    idx = Path(_index(root)["indexPath"])

    indexed = {json.loads(line)["rel_path"] for line in (idx / "files.jsonl").read_text().splitlines() if line}
    assert REFUSED not in indexed
    assert "app/clean.py" in indexed, "unrelated files must still be indexed"

    recorded = json.loads((idx / UNINDEXABLE_FILE).read_text(encoding="utf-8"))
    assert recorded == [REFUSED]
    assert "secret_thing" not in (idx / UNINDEXABLE_FILE).read_text(encoding="utf-8")


def test_a_genuinely_edited_file_is_still_detected(tmp_path: Path) -> None:
    """Guard the obvious over-correction: real changes must still mark the index stale."""
    root = _repo(tmp_path)
    ptr = _index(root)

    (root / "app" / "added.py").write_text("def added():\n    return 3\n", encoding="utf-8")
    fresh = index_freshness(root, ptr)

    assert fresh["state"] == "stale"
    assert "app/added.py" in fresh["changed"]
