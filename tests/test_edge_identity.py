"""Edge IDs must be unambiguous, and must fail closed when they are not.

The graph already rejects conflicting file and symbol IDs. Raw edges -- the
records `scanner.adapt()` emits with an `edge_id` -- were not checked at all, so
two edges claiming the same identity but pointing somewhere different were
accepted and later collapsed by a dict overwrite or `insert or replace`, with
the winner decided by input order.

These tests cover the helper AND the real index pipeline, because enforcing
this only inside `build_graph` would leave the persistence path bypassable.
"""

from __future__ import annotations

import json
import os
import random
from pathlib import Path

import pytest

from mimry.core.build import DuplicateIdentityError, GraphEngine, validate_edge_identity
from mimry.indexer import write_index
from mimry.storage import register_root, save_pointer


def _edge(edge_id="e1", source_id="f1", target_id="s1", edge_type="defines", confidence=1.0, **extra):
    return {
        "edge_id": edge_id,
        "source_type": "file",
        "source_id": source_id,
        "target_type": "symbol",
        "target_id": target_id,
        "edge_type": edge_type,
        "confidence": confidence,
        **extra,
    }


def _setup_pointer(root: Path, cache: Path, root_id: str) -> dict:
    pointer = {
        "rootId": root_id,
        "rootPath": str(root),
        "rootType": "repo",
        "indexPath": str(cache / "indexes" / root_id / "index"),
        "createdAt": "2026-01-01T00:00:00+00:00",
        "lastIndexedAt": None,
        "schemaVersion": "0.2.0",
    }
    os.environ["MIMRY_CACHE_HOME"] = str(cache)
    register_root(pointer)
    (root / ".mimry").mkdir(parents=True, exist_ok=True)
    save_pointer(root, pointer)
    return pointer


class TestEdgeIdentityPolicy:
    @pytest.mark.parametrize("edge_id", [None, "", "   "])
    def test_explicit_empty_edge_ids_are_rejected(self, edge_id):
        with pytest.raises(DuplicateIdentityError, match="non-empty string"):
            validate_edge_identity([_edge(edge_id=edge_id)])

    def test_conflicting_edge_ids_are_rejected(self):
        edges = [_edge(target_id="s1"), _edge(target_id="s2")]
        with pytest.raises(DuplicateIdentityError) as exc:
            validate_edge_identity(edges)
        assert "e1" in str(exc.value)

    @pytest.mark.parametrize(
        "field,value",
        [
            ("source_id", "f2"),
            ("target_id", "s2"),
            ("edge_type", "calls"),
            ("confidence", 0.5),
            ("origin", "python-ast"),
        ],
    )
    def test_conflicting_edge_ids_rejected_for_each_differing_field(self, field, value):
        """Any canonical field differing under one ID is ambiguous, not just endpoints."""
        edges = [_edge(), _edge(**{field: value})]
        with pytest.raises(DuplicateIdentityError):
            validate_edge_identity(edges)

    def test_exact_duplicate_edges_are_deduplicated_deterministically(self):
        edges = [_edge(), _edge()]
        result = validate_edge_identity(edges)
        assert len(result) == 1
        assert result == validate_edge_identity([_edge(), _edge(), _edge()])

    def test_edge_identity_result_is_stable_under_reversal_and_shuffle(self):
        edges = [_edge(edge_id=f"e{i}", target_id=f"s{i}") for i in range(12)]
        forward = json.dumps(validate_edge_identity(list(edges)), sort_keys=True)
        assert json.dumps(validate_edge_identity(list(reversed(edges))), sort_keys=True) == forward
        for seed in (0, 1, 7, 42, 1234):
            shuffled = list(edges)
            random.Random(seed).shuffle(shuffled)
            assert json.dumps(validate_edge_identity(shuffled), sort_keys=True) == forward

    def test_conflict_error_text_is_stable_under_reversal(self):
        """A conflict must report the same message whichever order it arrived in."""
        pair = [_edge(target_id="s1"), _edge(target_id="s2")]
        with pytest.raises(DuplicateIdentityError) as forward:
            validate_edge_identity(list(pair))
        with pytest.raises(DuplicateIdentityError) as backward:
            validate_edge_identity(list(reversed(pair)))
        assert str(forward.value) == str(backward.value)

    def test_graph_construction_rejects_conflicting_edge_ids(self):
        files = [{"file_id": "f1", "rel_path": "a.py", "extension": ".py"}]
        symbols = [{"symbol_id": "s1", "file_id": "f1", "name": "foo", "kind": "function", "language": "python"}]
        with pytest.raises(DuplicateIdentityError):
            GraphEngine().build_graph(files, symbols, [_edge(target_id="s1"), _edge(target_id="s2")])

    def test_file_and_symbol_duplicate_policies_unchanged(self):
        """Regression guard: the pre-existing node policies still fail closed."""
        engine = GraphEngine()
        with pytest.raises(DuplicateIdentityError):
            engine.build_graph(
                [
                    {"file_id": "f1", "rel_path": "a.py", "extension": ".py"},
                    {"file_id": "f1", "rel_path": "b.py", "extension": ".py"},
                ],
                [],
                [],
            )
        with pytest.raises(DuplicateIdentityError):
            engine.build_graph(
                [{"file_id": "f1", "rel_path": "a.py", "extension": ".py"}],
                [
                    {"symbol_id": "s1", "file_id": "f1", "name": "foo", "kind": "function", "language": "python"},
                    {"symbol_id": "s1", "file_id": "f1", "name": "bar", "kind": "function", "language": "python"},
                ],
                [],
            )


class TestEdgeIdentityThroughRealPipeline:
    """The helper is not the boundary that matters; `write_index` is."""

    def _repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        (repo / "src").mkdir(parents=True)
        (repo / "src" / "a.py").write_text("def alpha(): pass\n", encoding="utf-8")
        (repo / "src" / "b.py").write_text("def beta(): pass\n", encoding="utf-8")
        return repo

    def test_duplicate_edge_rejected_through_the_real_index_pipeline(self, tmp_path, monkeypatch):
        repo = self._repo(tmp_path)
        ptr = _setup_pointer(repo, tmp_path / "cache", "root-conflict")

        import mimry.scanner as scanner_module

        real_adapt = scanner_module.adapt

        def conflicting_adapt(path, root):
            f, symbols, edges, imports, exports, calls, references = real_adapt(path, root)
            # Both files now claim the same edge identity but point at different
            # targets -- exactly the ambiguity that used to be resolved by
            # whichever file the scanner happened to reach last.
            edges = [
                {
                    "edge_id": "collision",
                    "source_type": "file",
                    "source_id": f["file_id"],
                    "target_type": "symbol",
                    "target_id": f"sym-{f['rel_path']}",
                    "edge_type": "defines",
                    "confidence": 1.0,
                }
            ]
            return f, symbols, edges, imports, exports, calls, references

        monkeypatch.setattr("mimry.indexer.adapt", conflicting_adapt)

        with pytest.raises(DuplicateIdentityError) as exc:
            write_index(repo, ptr)
        assert "collision" in str(exc.value)

    def test_whitespace_edge_id_is_rejected_at_persistence_boundary(self, tmp_path, monkeypatch):
        repo = self._repo(tmp_path)
        ptr = _setup_pointer(repo, tmp_path / "cache", "root-empty-edge")
        import mimry.scanner as scanner_module

        real_adapt = scanner_module.adapt

        def empty_edge_adapt(path, root):
            f, symbols, edges, imports, exports, calls, references = real_adapt(path, root)
            if edges:
                edges[0]["edge_id"] = "  "
            return f, symbols, edges, imports, exports, calls, references

        monkeypatch.setattr("mimry.indexer.adapt", empty_edge_adapt)
        with pytest.raises(DuplicateIdentityError, match="non-empty string"):
            write_index(repo, ptr)

    def test_no_generation_is_published_when_edges_conflict(self, tmp_path, monkeypatch):
        repo = self._repo(tmp_path)
        cache = tmp_path / "cache"
        ptr = _setup_pointer(repo, cache, "root-nopublish")

        import mimry.scanner as scanner_module

        real_adapt = scanner_module.adapt

        def conflicting_adapt(path, root):
            f, symbols, _edges, imports, exports, calls, references = real_adapt(path, root)
            edges = [
                {
                    "edge_id": "collision",
                    "source_type": "file",
                    "source_id": f["file_id"],
                    "target_type": "symbol",
                    "target_id": f"sym-{f['rel_path']}",
                    "edge_type": "defines",
                    "confidence": 1.0,
                }
            ]
            return f, symbols, edges, imports, exports, calls, references

        monkeypatch.setattr("mimry.indexer.adapt", conflicting_adapt)

        with pytest.raises(DuplicateIdentityError):
            write_index(repo, ptr)

        generations = cache / "indexes" / "root-nopublish" / "generations"
        published = [p for p in generations.iterdir()] if generations.is_dir() else []
        assert published == [], f"a generation was published despite a fatal edge conflict: {published}"

    def test_exact_duplicate_edges_survive_the_real_pipeline(self, tmp_path, monkeypatch):
        """Harmless duplicates must not break indexing -- only conflicts fail."""
        repo = self._repo(tmp_path)
        ptr = _setup_pointer(repo, tmp_path / "cache", "root-dupe-ok")

        import mimry.scanner as scanner_module

        real_adapt = scanner_module.adapt

        def duplicating_adapt(path, root):
            f, symbols, edges, imports, exports, calls, references = real_adapt(path, root)
            return f, symbols, edges + list(edges), imports, exports, calls, references

        monkeypatch.setattr("mimry.indexer.adapt", duplicating_adapt)

        result = write_index(repo, ptr)
        assert result["files"] == 2
