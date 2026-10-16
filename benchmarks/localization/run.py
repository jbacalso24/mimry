"""Measure where MIMRY ranks the files a SWE-bench patch changes.

For each instance this checks out the repository at its base commit,
indexes it, and runs one `mimry find` with the issue text as the query.
The top 10 ranked files are scored against the gold files, which are
the files the gold patch modifies or deletes. Gold files are used only
for scoring.

This measures file retrieval from issue text, not issue resolution.
README.md has the protocol and the metrics.

`--set dev` runs the dev set instead of Verified (default: verified).

Run one instance locally:

    uv run python benchmarks/localization/run.py --work C:/tmp/loc \\
        --only psf__requests-1142 --out C:/tmp/loc-one.json

Run the dev set on the same work directory:

    uv run python benchmarks/localization/run.py --work C:/tmp/loc \\
        --set dev --only psf__requests --out C:/tmp/loc-dev-one.json

Merge shard outputs into one results file:

    uv run python benchmarks/localization/run.py \\
        --merge shard-0.json shard-1.json --out results.json
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from build_tasks import SETS, fetch_rows, task_path

from mimry.core.artifacts import load_graph, node_source_file
from mimry.intent import query_terms

TOP = 10
# `mimry find` prints "  %2d  <path>" per result. Reason lines are
# indented further.
RESULT_LINE = re.compile(r"^  ( \d|\d\d)  (.+)$")
# With -v, each result also prints its score and the signals behind it.
SCORE_LINE = re.compile(r"^      (score -?\d+: .*)$")
# --diagnose looks this deep for gold files ranked below the top 10.
DIAGNOSE_DEPTH = 50
# Diagnosis searches the file graph this many hops for a partner file.
PARTNER_HOPS = 3
# Neighbour records cover files one hop from the top results.
SEED_COUNT = 3
# A seed with more non-gold neighbours keeps this many of them.
NEIGHBOUR_SAMPLE = 200
LINKED_SYMBOL_CAP = 10
TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs"}
CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def score(ranked: list[str], gold: list[str]) -> dict:
    """Score one ranked list of paths against the gold files.

    A path that appears twice keeps its first rank. acc_at_k is True
    only when every gold file is in the top k. recall_at_k is the
    fraction of gold files in the top k. missed lists the gold files
    outside the top 10.
    """
    gold_set = set(gold)
    if not gold_set:
        raise ValueError("score needs at least one gold file")
    unique = list(dict.fromkeys(ranked))
    rank = {path: position for position, path in enumerate(unique, 1)}
    gold_ranks = sorted(rank[path] for path in gold_set if path in rank)

    def found(k: int) -> int:
        return sum(1 for position in gold_ranks if position <= k)

    top = set(unique[:TOP])
    return {
        "acc_at_1": found(1) == len(gold_set),
        "acc_at_5": found(5) == len(gold_set),
        "acc_at_10": found(TOP) == len(gold_set),
        "recall_at_5": found(5) / len(gold_set),
        "recall_at_10": found(TOP) / len(gold_set),
        "first_gold_rank": gold_ranks[0] if gold_ranks else None,
        "missed": sorted(path for path in gold_set if path not in top),
    }


def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None, ok=(0,)) -> str:
    out = subprocess.run(
        cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if out.returncode not in ok:
        raise RuntimeError(f"exit {out.returncode}: {out.stderr.strip()[-500:]}")
    return out.stdout


def checkout(work: Path, repo_name: str, sha: str) -> Path:
    """Clone once per repository, then reset to the base commit."""
    repo = work / "repos" / repo_name.replace("/", "__")
    if not repo.exists():
        repo.parent.mkdir(parents=True, exist_ok=True)
        try:
            run(
                [
                    "git",
                    "clone",
                    "-q",
                    "--filter=blob:none",
                    "--no-checkout",
                    f"https://github.com/{repo_name}.git",
                    str(repo),
                ],
                work,
            )
        except RuntimeError:
            shutil.rmtree(repo, ignore_errors=True)
            raise
    try:
        run(["git", "checkout", "-q", "-f", "--detach", sha], repo)
    except RuntimeError:
        # The base commit may be on a branch the clone did not fetch.
        run(["git", "fetch", "-q", "--filter=blob:none", "origin", sha], repo)
        run(["git", "checkout", "-q", "-f", "--detach", sha], repo)
    run(["git", "clean", "-q", "-fdx", "-e", ".mimry"], repo)
    return repo


def mimry(repo: Path, env: dict[str, str], *args: str) -> str:
    # find exits 1 when nothing matches: an empty ranking.
    ok = (0, 1) if args[0] == "find" else (0,)
    return run([sys.executable, "-m", "mimry.cli", "--root", str(repo), *args], repo, env, ok)


def parse_find(output: str, depth: int = TOP) -> list[str]:
    """The ranked paths printed by `mimry find`, in rank order."""
    return [path for path, _ in parse_find_explained(output, depth)]


def parse_find_explained(output: str, depth: int = TOP) -> list[tuple[str, str | None]]:
    """Ranked paths with the score line `find -v` prints, if any."""
    results: list[tuple[str, str | None]] = []
    for line in output.splitlines():
        match = RESULT_LINE.match(line)
        if match:
            if int(match.group(1)) != len(results) + 1:
                raise RuntimeError("mimry find printed its results out of rank order")
            results.append((match.group(2), None))
            continue
        explained = SCORE_LINE.match(line)
        if explained and results and results[-1][1] is None:
            results[-1] = (results[-1][0], explained.group(1))
    if not results and "No files match" not in output:
        raise RuntimeError("mimry find printed no result list")
    return results[:depth]


def _file_edges(graph: dict) -> tuple[dict[str, dict], list[tuple[str, str, str, str, str]]]:
    """Graph nodes by id, and the edges that join two files.

    Each edge is (source file, target file, relation, source id,
    target id). Edges with an endpoint outside the graph, or with both
    ends in one file, are dropped.
    """
    nodes = {
        str(node["id"]): node for node in graph.get("nodes") or [] if node.get("id") is not None
    }

    def file_of(end: object) -> str | None:
        node = nodes.get(str(end)) if end else None
        return node_source_file(node) if node else None

    edges = []
    for edge in graph.get("links") or graph.get("edges") or []:
        source_id = edge.get("source") or edge.get("from")
        target_id = edge.get("target") or edge.get("to")
        source, target = file_of(source_id), file_of(target_id)
        if not source or not target or source == target:
            continue
        relation = edge.get("relation") or edge.get("type") or "relates"
        edges.append((source, target, relation, str(source_id), str(target_id)))
    return nodes, edges


def partner_links(
    graph: dict, gold: list[str], top: list[str], rank: dict[str, int | None]
) -> dict:
    """How each gold file outside the top 10 links to the top 10.

    `graph` is MIMRY's graph (nodes and links or edges). Edges are taken
    between files, pairs within one file are dropped, and hops are
    undirected and searched at most PARTNER_HOPS deep.
    """
    top_set = set(top)
    found = {path for path in gold if path in top_set}

    neighbours: dict[str, set[str]] = defaultdict(set)
    relations: dict[tuple[str, str], set[str]] = defaultdict(set)
    _, edges = _file_edges(graph)
    for source, target, relation, _, _ in edges:
        relations[(source, target)].add(relation)
        neighbours[source].add(target)
        neighbours[target].add(source)

    def nearest(start: str, targets: set[str]) -> int | None:
        seen = {start}
        frontier = {start}
        for hops in range(1, PARTNER_HOPS + 1):
            frontier = {n for f in frontier for n in neighbours[f]} - seen
            if frontier & targets:
                return hops
            seen |= frontier
        return None

    partners = {}
    for path in sorted({path for path in gold if path not in top_set}):
        direct = []
        for other in sorted(top_set):
            out = relations.get((path, other), set())
            back = relations.get((other, path), set())
            if out or back:
                direction = "both" if out and back else "out" if out else "in"
                direct.append(
                    {"file": other, "relations": sorted(out | back), "direction": direction}
                )
        partners[path] = {
            "rank": rank.get(path),
            "hops_to_found_gold": nearest(path, found),
            "hops_to_top10": nearest(path, top_set),
            "direct": direct,
            "same_dir_as_found_gold": any(
                posixpath.dirname(g) == posixpath.dirname(path) for g in found
            ),
        }
    return partners


def _tokens(text: str) -> set[str]:
    """Lower-case words of a name or path, split on case and symbols."""
    return set(re.findall(r"[a-z0-9]+", CAMEL_BOUNDARY.sub(" ", text).lower()))


def _top_package(path: str) -> str | None:
    head, sep, _ = path.partition("/")
    return head if sep else None


def _stem(path: str) -> str:
    """The name a file is about: no folder, extensions or test affix.

    index.test.ts -> index, test_foo.py -> foo, foo_test.py -> foo.
    """
    stem = posixpath.basename(path).lower().split(".")[0]
    stem = re.sub(r"^test[_-]", "", stem)
    return re.sub(r"[_-]test$", "", stem)


def _is_test(path: str) -> bool:
    *folders, name = path.lower().split("/")
    return (
        any(folder in TEST_DIRS for folder in folders)
        or name.startswith("test_")
        or any(marker in name for marker in ("_test.", "-test.", ".test.", ".spec."))
    )


def _neighbour_record(
    seed: str,
    seed_rank: int,
    seed_is_gold: bool,
    other: str,
    between: dict[str, dict[str, list[tuple[str, str, str]]]],
    nodes: dict[str, dict],
    rank: dict[str, int],
    gold_set: set[str],
    terms: list[str],
) -> dict:
    links = between[seed][other]
    directions = {direction for direction, _, _ in links}
    direction = "both" if len(directions) == 2 else next(iter(directions))
    # Names on the neighbour's side of the edges. File nodes are
    # skipped: their label is a path, not a symbol.
    linked = sorted(
        {
            str(nodes[node_id]["label"])
            for _, _, node_id in links
            if node_id in nodes
            and nodes[node_id].get("type") != "file"
            and nodes[node_id].get("label")
        }
    )
    symbol_words = _tokens(" ".join(linked))
    path_words = _tokens(other)
    return {
        "seed_rank": seed_rank,
        "seed_is_gold": seed_is_gold,
        "file": other,
        "is_gold": other in gold_set,
        "rank": rank.get(other),
        "relations": sorted({relation for _, relation, _ in links}),
        "direction": direction,
        "edge_count": len(links),
        "linked_symbols": linked[:LINKED_SYMBOL_CAP],
        "linked_symbol_query_overlap": len({term for term in terms if term in symbol_words}),
        "neighbour_degree": len(between[other]),
        "same_dir": posixpath.dirname(seed) == posixpath.dirname(other),
        "same_top_package": _top_package(seed) is not None
        and _top_package(seed) == _top_package(other),
        "name_stem_match": _stem(seed) != "" and _stem(seed) == _stem(other),
        "is_test": _is_test(other),
        "query_overlap_path": len({term for term in terms if term in path_words}),
    }


def neighbour_records(
    graph: dict, query: str, explained: list[tuple[str, str | None]], gold: list[str]
) -> list[dict]:
    """Features of each file one hop from the top SEED_COUNT results.

    `direction` is read from the seed: "out" means the seed links to
    the neighbour. A seed with more than NEIGHBOUR_SAMPLE non-gold
    neighbours keeps every gold one and NEIGHBOUR_SAMPLE non-gold ones
    spaced evenly through the file-sorted list. `neighbour_total`
    counts all of them.
    """
    nodes, edges = _file_edges(graph)
    gold_set = set(gold)
    terms = query_terms(query)
    rank = {path: position for position, (path, _) in enumerate(explained, 1)}
    # between[a][b]: the edges joining files a and b, each as
    # (direction from a, relation, node id on b's side).
    between: dict[str, dict[str, list[tuple[str, str, str]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for source, target, relation, source_id, target_id in edges:
        between[source][target].append(("out", relation, target_id))
        between[target][source].append(("in", relation, source_id))

    records = []
    for seed_rank, (seed, _) in enumerate(explained[:SEED_COUNT], 1):
        seed_is_gold = seed in gold_set
        items = [
            _neighbour_record(
                seed, seed_rank, seed_is_gold, other, between, nodes, rank, gold_set, terms
            )
            for other in sorted(between.get(seed, {}))
        ]
        gold_items = [item for item in items if item["is_gold"]]
        other_items = [item for item in items if not item["is_gold"]]
        if len(other_items) > NEIGHBOUR_SAMPLE:
            count = len(other_items)
            other_items = [
                other_items[i * count // NEIGHBOUR_SAMPLE] for i in range(NEIGHBOUR_SAMPLE)
            ]
        records.append(
            {
                "seed_rank": seed_rank,
                "seed": seed,
                "seed_is_gold": seed_is_gold,
                "neighbour_total": len(items),
                "neighbours": sorted(gold_items + other_items, key=lambda item: item["file"]),
            }
        )
    return records


def diagnosis(
    explained: list[tuple[str, str | None]],
    gold: list[str],
    graph: dict | None = None,
    query: str = "",
) -> dict:
    """Where each gold file ranked within DIAGNOSE_DEPTH, how the gold
    files outside the top 10 connect to the top 10, the neighbours of
    the top results, and why the top 10 and those gold files scored as
    they did."""
    rank = {path: position for position, (path, _) in enumerate(explained, 1)}
    keep = {path for path, _ in explained[:TOP]} | set(gold)
    gold_rank = {path: rank.get(path) for path in sorted(gold)}
    top = [path for path, _ in explained[:TOP]]
    graph = graph or {}
    return {
        "gold_rank": gold_rank,
        "partners": partner_links(graph, gold, top, gold_rank),
        "neighbours": neighbour_records(graph, query, explained, gold),
        "explain": [
            {"rank": position, "path": path, "score": line}
            for position, (path, line) in enumerate(explained, 1)
            if path in keep
        ],
    }


def load_problems(work: Path, set_name: str, tasks: list[dict]) -> dict[str, dict[str, str]]:
    """Issue text and base commit for every task in the set.

    The download is cached in the work directory, so a shard fetches the
    dataset once rather than once per instance. The cache is named per
    set and keeps only the instances in the task file, so every shard
    reads the same cache and one work directory can hold both sets.
    """
    cache = work / f"problem-statements.{set_name}.json"
    if not cache.exists():
        wanted = {task["instance_id"] for task in tasks}
        spec = SETS[set_name]
        problems = {
            row["instance_id"]: {
                "base_commit": row["base_commit"],
                "problem_statement": row["problem_statement"],
            }
            for row in fetch_rows(spec["dataset"], spec["rows"])
            if row["instance_id"] in wanted
        }
        missing = wanted - problems.keys()
        if missing:
            raise RuntimeError(
                f"{len(missing)} tasks have no issue text, e.g. {sorted(missing)[0]}"
            )
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps(problems, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
    return json.loads(cache.read_text(encoding="utf-8"))


def run_instance(
    task: dict, problem: dict[str, str], work: Path, env: dict[str, str], diagnose: bool = False
) -> dict:
    row = {
        "instance_id": task["instance_id"],
        "repo": task["repo"],
        "base_commit": task["base_commit"],
        "gold": task["gold"],
        "added": task["added"],
    }
    if not task["gold"]:
        return row | {"status": "no_gold"}
    if problem["base_commit"] != task["base_commit"]:
        return row | {"status": "error", "error": "base commit differs from the dataset"}
    stage = "checkout"
    try:
        repo = checkout(work, task["repo"], task["base_commit"])
        stage = "init"
        if not (repo / ".mimry" / "config.toml").exists():
            mimry(repo, env, "init", "--skip-graph")
        stage = "index"
        started = time.perf_counter_ns()
        mimry(repo, env, "index")
        index_ms = (time.perf_counter_ns() - started) / 1_000_000
        stage = "lookup"
        started = time.perf_counter_ns()
        # "--" stops an issue text starting with "-" as an option.
        query = problem["problem_statement"]
        if diagnose:
            # The same ranking, deeper and with -v; the top 10 are
            # scored exactly as in a normal run.
            found = mimry(repo, env, "find", "--limit", str(DIAGNOSE_DEPTH), "-v", "--", query)
            explained = parse_find_explained(found, DIAGNOSE_DEPTH)
            ranked = [path for path, _ in explained[:TOP]]
        else:
            found = mimry(repo, env, "find", "--limit", str(TOP), "--", query)
            ranked = parse_find(found)
        lookup_ms = (time.perf_counter_ns() - started) / 1_000_000
        # The repo-local graph `mimry index` just wrote, read after the
        # lookup is timed.
        graph = load_graph(repo) if diagnose else None
    except (RuntimeError, OSError) as exc:
        return row | {"status": "error", "error": f"{stage} failed: {exc}"}
    return (
        row
        | {
            "status": "scored",
            "ranked": ranked,
            "index_ms": round(index_ms),
            "lookup_ms": round(lookup_ms),
        }
        | score(ranked, task["gold"])
        | ({"diagnosis": diagnosis(explained, task["gold"], graph, query)} if diagnose else {})
    )


def select(
    tasks: list[dict], shard: str | None, only: str | None, limit: int | None
) -> list[dict]:
    tasks = sorted(tasks, key=lambda task: task["instance_id"])
    if shard:
        index, count = (int(part) for part in shard.split("/"))
        if not 0 <= index < count:
            raise SystemExit(f"--shard {shard}: I must be between 0 and N-1")
        tasks = [task for position, task in enumerate(tasks) if position % count == index]
    if only:
        tasks = [task for task in tasks if only in task["instance_id"]]
    if limit is not None:
        tasks = tasks[:limit]
    return tasks


def summarize(rows: list[dict]) -> dict:
    scored = [row for row in rows if row["status"] == "scored"]

    def pct(values: list) -> float | None:
        return round(100 * sum(values) / len(values), 1) if values else None

    per_repo = {}
    for repo in sorted({row["repo"] for row in scored}):
        group = [row for row in scored if row["repo"] == repo]
        per_repo[repo] = {
            "scored": len(group),
            "acc_at_10": pct([row["acc_at_10"] for row in group]),
        }
    return {
        "instances": len(rows),
        "scored": len(scored),
        "errors": sum(row["status"] == "error" for row in rows),
        "no_gold": sum(row["status"] == "no_gold" for row in rows),
        "acc_at_1": pct([row["acc_at_1"] for row in scored]),
        "acc_at_5": pct([row["acc_at_5"] for row in scored]),
        "acc_at_10": pct([row["acc_at_10"] for row in scored]),
        "mean_recall_at_5": pct([row["recall_at_5"] for row in scored]),
        "mean_recall_at_10": pct([row["recall_at_10"] for row in scored]),
        "per_repo": per_repo,
    }


def write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )


def merge(paths: list[Path], out: Path) -> int:
    rows: list[dict] = []
    for path in paths:
        rows.extend(json.loads(path.read_text(encoding="utf-8"))["instances"])
    counts = Counter(row["instance_id"] for row in rows)
    duplicates = sorted(instance for instance, count in counts.items() if count > 1)
    if duplicates:
        raise SystemExit(f"instances appear in more than one shard: {duplicates[:5]}")
    rows.sort(key=lambda row: row["instance_id"])
    summary = summarize(rows)
    write(out, {"instances": rows, "summary": summary})
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--work", type=Path, help="Scratch directory for clones, the MIMRY cache and issue text"
    )
    parser.add_argument("--shard", help="I/N: run the instances at positions I mod N")
    parser.add_argument("--only", help="Run only instance ids containing this text")
    parser.add_argument("--max", dest="limit", type=int, help="Run at most this many instances")
    parser.add_argument("--out", type=Path, required=True, help="Results JSON file")
    parser.add_argument(
        "--set",
        dest="set_name",
        choices=sorted(SETS),
        default="verified",
        help="Task set to run (default: verified)",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help=f"Also record gold ranks to {DIAGNOSE_DEPTH} and the score line of each result",
    )
    parser.add_argument(
        "--merge", type=Path, nargs="+", help="Merge these shard files into --out and exit"
    )
    args = parser.parse_args(argv)
    if args.merge:
        return merge(args.merge, args.out)
    if args.work is None:
        parser.error("--work is required to run instances")
    work = args.work.resolve()
    env = {**os.environ, "MIMRY_CACHE_HOME": str(work / "cache"), "PYTHONIOENCODING": "utf-8"}
    all_tasks = json.loads(task_path(args.set_name).read_text(encoding="utf-8"))
    tasks = select(all_tasks, args.shard, args.only, args.limit)
    problems = load_problems(work, args.set_name, all_tasks)
    rows = []
    for task in tasks:
        row = run_instance(task, problems[task["instance_id"]], work, env, args.diagnose)
        rows.append(row)
        detail = (
            f"acc@10 {row['acc_at_10']} first {row['first_gold_rank']}"
            f" index {row['index_ms']} ms lookup {row['lookup_ms']} ms"
            if row["status"] == "scored"
            else row.get("error", row["status"])
        )
        print(f"{row['instance_id']:<36} {row['status']:<8} {detail}", file=sys.stderr)
    payload = {"instances": rows, "summary": summarize(rows)}
    write(args.out, payload)
    summary = payload["summary"]
    print(
        f"{summary['scored']} scored, {summary['errors']} errors, {summary['no_gold']} no gold"
        f" of {summary['instances']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
