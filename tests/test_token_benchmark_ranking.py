"""rank_metrics and the ranking block of the token benchmark summary."""

import importlib.util
import sys
import types
from pathlib import Path

RUN_PY = Path(__file__).resolve().parents[1] / "benchmarks" / "tokens" / "run.py"


class _StubEncoding:
    def encode(self, text, **_kwargs):
        return list(text)  # one token per character; counts only need to be positive


def _load_run(monkeypatch):
    # run.py imports tiktoken at module level and builds its encoding
    # at import time. tiktoken is not a dev dependency and the real
    # encoding needs network, so swap in a stub for this test.
    stub = types.ModuleType("tiktoken")
    stub.get_encoding = lambda _name: _StubEncoding()
    monkeypatch.setitem(sys.modules, "tiktoken", stub)
    spec = importlib.util.spec_from_file_location("token_benchmark_run", RUN_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gold_at_rank_one(monkeypatch):
    m = _load_run(monkeypatch).rank_metrics(["a", "b", "c"], ["a"])

    assert m == {
        "recall_at_5": 1.0,
        "recall_at_10": 1.0,
        "first_gold_rank": 1,
        "missed": [],
    }


def test_gold_at_ranks_three_and_seven(monkeypatch):
    ranked = ["x", "y", "g1", "z", "w", "u", "g2"]
    m = _load_run(monkeypatch).rank_metrics(ranked, ["g1", "g2"])

    assert m["recall_at_5"] == 0.5
    assert m["recall_at_10"] == 1.0
    assert m["first_gold_rank"] == 3
    assert m["missed"] == []


def test_one_gold_file_missing(monkeypatch):
    m = _load_run(monkeypatch).rank_metrics(["a", "b"], ["a", "z"])

    assert m == {
        "recall_at_5": 0.5,
        "recall_at_10": 0.5,
        "first_gold_rank": 1,
        "missed": ["z"],
    }


def test_duplicate_path_keeps_first_rank(monkeypatch):
    m = _load_run(monkeypatch).rank_metrics(["a", "a", "b"], ["b"])

    assert m["first_gold_rank"] == 2
    assert m["recall_at_5"] == 1.0


def test_empty_ranked_list(monkeypatch):
    m = _load_run(monkeypatch).rank_metrics([], ["a", "b"])

    assert m == {
        "recall_at_5": 0.0,
        "recall_at_10": 0.0,
        "first_gold_rank": None,
        "missed": ["a", "b"],
    }


def test_summarize_reports_ranking_per_agent(monkeypatch):
    run = _load_run(monkeypatch)
    rows = [
        {
            "repo_tokens": 1000,
            "grep": {
                "tokens": 100,
                "success": True,
                "ranking": run.rank_metrics(["a", "x", "y", "z", "w", "q", "b"], ["a", "b"]),
            },
            "mimry": {
                "tokens": 50,
                "success": True,
                "ranking": run.rank_metrics(["x", "b"], ["a", "b"]),
            },
        },
        {
            "repo_tokens": 1000,
            "grep": {
                "tokens": 100,
                "success": False,
                "ranking": run.rank_metrics(["c"], ["c", "d"]),
            },
            "mimry": {
                "tokens": 50,
                "success": True,
                "ranking": run.rank_metrics(["c", "d", "e"], ["c", "d"]),
            },
        },
    ]

    summary = run.summarize(rows)

    assert summary["ranking"] == {
        "grep": {
            "mean_recall_at_5_pct": 50.0,
            "mean_recall_at_10_pct": 75.0,
            "median_first_gold_rank": 1,
            "tasks_with_missed_files": 1,
        },
        "mimry": {
            "mean_recall_at_5_pct": 75.0,
            "mean_recall_at_10_pct": 75.0,
            "median_first_gold_rank": 1.5,
            "tasks_with_missed_files": 1,
        },
    }
    assert summary["tasks"] == 2
    assert summary["success"] == {"grep": 1, "mimry": 2}
