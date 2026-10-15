"""Unit tests for the feedback replay scorer and comparison.

Hand-made rank lists only. No mimry, git or network.
"""

import importlib.util
from pathlib import Path

FEEDBACK = Path(__file__).resolve().parents[1] / "benchmarks" / "feedback"


def _load_run():
    spec = importlib.util.spec_from_file_location("feedback_replay_run", FEEDBACK / "run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


replay = _load_run()


def test_helped_when_gold_moves_up_and_none_moves_down():
    off = replay.score_arm(["a.py", "x.py", "b.py", "y.py", "z.py", "gold.py"], ["gold.py"])
    on = replay.score_arm(["gold.py", "a.py", "x.py", "b.py", "y.py", "z.py"], ["gold.py"])
    assert replay.compare(off, on) == {"helped": True, "hurt": False, "pushed_out_of_top5": []}
    assert off["reciprocal_rank"] == 1 / 6
    assert on["hit_at_1"] is True


def test_hurt_when_gold_moves_down():
    off = replay.score_arm(["gold.py", "a.py", "b.py"], ["gold.py"])
    on = replay.score_arm(["a.py", "b.py", "c.py", "gold.py"], ["gold.py"])
    assert replay.compare(off, on) == {"helped": False, "hurt": True, "pushed_out_of_top5": []}


def test_pushed_out_of_top5_is_listed_with_both_ranks():
    off = replay.score_arm(["a.py", "b.py", "c.py", "gold.py"], ["gold.py"])
    on = replay.score_arm(["a.py", "b.py", "c.py", "d.py", "e.py", "f.py", "gold.py"], ["gold.py"])
    assert replay.compare(off, on) == {
        "helped": False,
        "hurt": True,
        "pushed_out_of_top5": [{"path": "gold.py", "off_rank": 4, "on_rank": 7}],
    }


def test_no_gold_found_in_either_arm_is_neither_helped_nor_hurt():
    off = replay.score_arm(["a.py"], ["gold.py"])
    on = replay.score_arm(["b.py"], ["gold.py"])
    assert replay.compare(off, on) == {"helped": False, "hurt": False, "pushed_out_of_top5": []}
    assert off["gold_ranks"] == {"gold.py": None}
    assert off["hit_at_10"] is False
    assert off["reciprocal_rank"] == 0.0
