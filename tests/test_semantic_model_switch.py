"""The semantic model switch and RRF fusion. Both are off when unset."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from mimry import semantic
from mimry.semantic import (
    SEMANTIC_BACKEND,
    build_semantic_index,
    merge_semantic_rows,
    semantic_health,
    semantic_rows,
)

ROOT = "root-1"
MODEL = "fake/model"


class FakeModel:
    """Embeds text into two dimensions: auth-ish text points along x."""

    def encode(self, texts):
        return [_fake_vector(text) for text in texts]


def _fake_vector(text: str) -> list[float]:
    if "login" in text or "auth" in text:
        return [1.0, 0.0]
    if "notes" in text:
        # Normalises to about 0.148 on x, under the 0.2 floor.
        return [0.15, 1.0]
    return [0.0, 1.0]


@pytest.fixture
def clean_env(monkeypatch):
    for name in (semantic.MODEL_ENV, semantic.FUSION_ENV, semantic.WEIGHT_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(semantic, "_STATIC_MODELS", {})
    return monkeypatch


def _write_index(idx: Path) -> None:
    files = [
        {
            "file_id": "f1",
            "rel_path": "auth/session.py",
            "filename": "session.py",
            "extension": ".py",
            "content_hint": "def authenticate(user, password)",
        },
        {
            "file_id": "f2",
            "rel_path": "util/strings.py",
            "filename": "strings.py",
            "extension": ".py",
            "content_hint": "pad left right",
        },
        {
            "file_id": "f3",
            "rel_path": "docs/notes.md",
            "filename": "notes.md",
            "extension": ".md",
            "content_hint": "unrelated words",
        },
    ]
    symbols = [{"file_id": "f1", "name": "authenticate"}]
    idx.mkdir(parents=True, exist_ok=True)
    (idx / "files.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in files), encoding="utf-8"
    )
    (idx / "symbols.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in symbols), encoding="utf-8"
    )
    con = sqlite3.connect(idx / "mimry.sqlite")
    con.execute("create table index_generation(generation_id text)")
    con.commit()
    con.close()


def test_defaults_are_the_hash_backend(clean_env):
    assert semantic.active_backend() == SEMANTIC_BACKEND
    assert semantic.fusion_mode() == "bonus"
    clean_env.setenv(semantic.MODEL_ENV, "   ")
    assert semantic.active_backend() == SEMANTIC_BACKEND


def test_bonus_merge_is_unchanged_without_variables(clean_env):
    base = [
        {"path": "a.py", "score": 50, "reason": "exact name"},
        {"path": "c.py", "score": 30, "reason": "graph"},
    ]
    sem = [
        {"path": "a.py", "score": 40, "reason": "semantic match", "details": "path: a"},
        {"path": "b.py", "score": 90, "reason": "semantic match", "details": "symbol: b"},
    ]
    merged = merge_semantic_rows(base, sem, limit=10)
    # a gets a 14 point bonus; b is semantic-only and capped at 80.
    assert [(row["path"], row["score"]) for row in merged] == [
        ("b.py", 80),
        ("a.py", 64),
        ("c.py", 30),
    ]
    assert merged[1]["reason"] == "exact name, semantic match"


@pytest.mark.parametrize(
    ("weight", "order"),
    [
        # fused: x = 1/61 + W/62, y = 1/62, z = W/61
        ("2.0", ["x.py", "z.py", "y.py"]),
        (None, ["x.py", "y.py", "z.py"]),
    ],
)
def test_rrf_follows_the_formula_and_keeps_base_scores(clean_env, weight, order):
    clean_env.setenv(semantic.FUSION_ENV, "rrf")
    if weight is not None:
        clean_env.setenv(semantic.WEIGHT_ENV, weight)
    base = [
        {"path": "x.py", "score": 60, "reason": "fts"},
        {"path": "y.py", "score": 40, "reason": "fts"},
    ]
    # z.py is semantic-only and ranks first semantically; x.py ranks
    # second there.
    sem = [
        {"path": "z.py", "score": 70, "reason": "semantic match", "details": "d"},
        {"path": "x.py", "score": 10, "reason": "semantic match", "details": "e"},
    ]
    merged = merge_semantic_rows(base, sem, limit=10)
    assert [row["path"] for row in merged] == order
    # Base scores go out in fused order; the third row steps down one
    # point from the smallest base score.
    assert [row["score"] for row in merged] == [60, 40, 39]
    x_row = next(row for row in merged if row["path"] == "x.py")
    assert x_row["reason"] == "fts, semantic match"


def test_model_path_ranks_the_closest_file_and_drops_weak_chunks(tmp_path, clean_env):
    clean_env.setenv(semantic.MODEL_ENV, MODEL)
    clean_env.setattr(semantic, "_load_static_model", lambda name: FakeModel())
    _write_index(tmp_path)
    build_semantic_index(tmp_path, ROOT)

    health = semantic_health(tmp_path, ROOT)
    assert health["status"] == "current"
    assert health["backend"] == f"model2vec:{MODEL}"

    rows, _ = semantic_rows(tmp_path, ROOT, "login")
    assert rows[0]["path"] == "auth/session.py"
    paths = {row["path"] for row in rows}
    assert "util/strings.py" not in paths
    assert "docs/notes.md" not in paths


def test_switching_the_backend_makes_the_index_stale(tmp_path, clean_env):
    _write_index(tmp_path)
    build_semantic_index(tmp_path, ROOT)
    assert semantic_health(tmp_path, ROOT)["status"] == "current"

    clean_env.setenv(semantic.MODEL_ENV, MODEL)
    clean_env.setattr(semantic, "_load_static_model", lambda name: FakeModel())
    assert semantic_health(tmp_path, ROOT)["status"] == "stale"
    assert semantic_rows(tmp_path, ROOT, "login")[0] == []

    build_semantic_index(tmp_path, ROOT)
    assert semantic_health(tmp_path, ROOT)["status"] == "current"


def test_missing_model2vec_raises_a_clear_error(clean_env):
    clean_env.setenv(semantic.MODEL_ENV, MODEL)
    clean_env.setitem(sys.modules, "model2vec", None)
    with pytest.raises(RuntimeError, match="model2vec") as caught:
        semantic._embed(["login"], MODEL)
    assert semantic.MODEL_ENV in str(caught.value)


def test_cap_per_file_keeps_each_files_best_chunks():
    hits = [
        ("a.py", "path", 90, ""),
        ("a.py", "symbol", 80, ""),
        ("a.py", "content_hint", 70, ""),
        ("a.py", "adapter_fact", 60, ""),
        ("b.py", "path", 50, ""),
    ]
    kept = semantic._cap_per_file(hits, 3)
    assert [(path, kind) for path, kind, _score, _text in kept] == [
        ("a.py", "path"),
        ("a.py", "symbol"),
        ("a.py", "content_hint"),
        ("b.py", "path"),
    ]
