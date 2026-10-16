"""Score partner rules on a `run.py --diagnose` results file, offline.

For each rule over the neighbour features, the report gives the
precision among neighbours of gold seeds (gold neighbours / neighbours
with the rule) and how many missing gold partners the rule reaches. A
missing gold partner is a gold file outside the top 10 in an instance
where another gold file is in the top 10.

Non-gold neighbours are sampled by run.py, so each one stands for
several: a seed's unsampled non-gold neighbours are spread evenly over
the sampled ones. Gold neighbours are never sampled.

    uv run python benchmarks/localization/partner_report.py results.json
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

TOP = 10

Rule = Callable[[dict], bool]

RULES: list[tuple[str, Rule]] = [
    ("any neighbour", lambda n: True),
    ("edge_count >= 2", lambda n: n["edge_count"] >= 2),
    ("edge_count >= 3", lambda n: n["edge_count"] >= 3),
    ("linked_symbol_query_overlap >= 1", lambda n: n["linked_symbol_query_overlap"] >= 1),
    ("query_overlap_path >= 1", lambda n: n["query_overlap_path"] >= 1),
    ("name_stem_match", lambda n: n["name_stem_match"]),
    ("same_dir", lambda n: n["same_dir"]),
    ("same_top_package", lambda n: n["same_top_package"]),
    ("direction == out", lambda n: n["direction"] == "out"),
    ("direction == both", lambda n: n["direction"] == "both"),
    ("not is_test", lambda n: not n["is_test"]),
    ("neighbour_degree <= 5", lambda n: n["neighbour_degree"] <= 5),
    (
        "direction == out and same_top_package",
        lambda n: n["direction"] == "out" and n["same_top_package"],
    ),
    (
        "edge_count >= 2 and linked_symbol_query_overlap >= 1",
        lambda n: n["edge_count"] >= 2 and n["linked_symbol_query_overlap"] >= 1,
    ),
    ("edge_count >= 2 and not is_test", lambda n: n["edge_count"] >= 2 and not n["is_test"]),
    ("name_stem_match or same_dir", lambda n: n["name_stem_match"] or n["same_dir"]),
]


def _gather(
    instances: list[dict],
) -> tuple[list[tuple[str, dict, float]], set[tuple[str, str]], int]:
    """Gold-seed neighbours with their weights, the missing gold
    partners, and how many instances carried neighbour records."""
    items: list[tuple[str, dict, float]] = []
    missing: set[tuple[str, str]] = set()
    with_records = 0
    for instance in instances:
        diagnosis = instance.get("diagnosis") or {}
        if instance.get("status") != "scored" or "neighbours" not in diagnosis:
            continue
        with_records += 1
        instance_id = instance["instance_id"]
        gold_rank = diagnosis["gold_rank"]
        if any(rank is not None and rank <= TOP for rank in gold_rank.values()):
            missing |= {
                (instance_id, path)
                for path, rank in gold_rank.items()
                if rank is None or rank > TOP
            }
        for seed in diagnosis["neighbours"]:
            if not seed["seed_is_gold"]:
                continue
            # Gold neighbours are all recorded. Each kept non-gold one
            # stands for the unsampled non-gold neighbours too.
            kept_gold = sum(1 for n in seed["neighbours"] if n["is_gold"])
            kept_others = len(seed["neighbours"]) - kept_gold
            other_weight = (
                (seed["neighbour_total"] - kept_gold) / kept_others if kept_others else 1.0
            )
            for neighbour in seed["neighbours"]:
                weight = 1.0 if neighbour["is_gold"] else other_weight
                items.append((instance_id, neighbour, weight))
    return items, missing, with_records


def _percent(part: float, whole: float) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "n/a"


def partner_features_report(results_path: Path | str) -> str:
    """The Markdown report for one --diagnose results file."""
    data = json.loads(Path(results_path).read_text(encoding="utf-8"))
    items, missing, with_records = _gather(data["instances"])
    gold_total = sum(1 for _, neighbour, _ in items if neighbour["is_gold"])
    lines = [
        "# Partner feature report",
        "",
        f"Instances with neighbour records: {with_records}.",
        f"Gold neighbours of gold seeds: {gold_total}.",
        f"Missing gold partners: {len(missing)}.",
        "",
        "Precision is over neighbours of gold seeds. Neighbour counts are estimates,",
        "because non-gold neighbours are sampled. Coverage is the share of missing",
        "gold partners that some gold seed's neighbour with the rule reaches.",
        "",
        "| Rule | Neighbours | Gold | Precision | Missing partners reached |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, rule in RULES:
        matched = [(instance_id, n, w) for instance_id, n, w in items if rule(n)]
        estimate = sum(w for _, _, w in matched)
        gold = sum(1 for _, n, _ in matched if n["is_gold"])
        reached = {(i, n["file"]) for i, n, _ in matched if n["is_gold"]} & missing
        lines.append(
            f"| {name} | {estimate:.0f} | {gold} | {_percent(gold, estimate)} "
            f"| {len(reached)} of {len(missing)} ({_percent(len(reached), len(missing))}) |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("results", type=Path, help="results JSON written by run.py --diagnose")
    args = parser.parse_args(argv)
    print(partner_features_report(args.results), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
