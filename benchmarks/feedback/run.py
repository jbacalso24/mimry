"""Replay recorded feedback over a repository's own commit history.

Each of the last N non-merge commits is a task. Its subject line is the
query, and the files it added or modified that still exist at HEAD are
the gold files. Two arms run the same tasks in the same order. The off
arm runs `mimry find` only. The on arm also records each task's top 10
suggestions and its gold files as feedback, so later tasks can use it.

The replay works on a clone of HEAD, so --repo is only read. README.md
has the protocol, the metrics and what is not covered.

Run on a small repository:

    uv run python benchmarks/feedback/run.py --repo C:/path/to/repo \\
        --commits 8 --work C:/tmp/replay-work \\
        --out C:/tmp/replay-smoke.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import re
import shutil
import stat
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCALIZATION = HERE.parent / "localization"
# The localization runner provides mimry(), run() and parse_find().
# It imports build_tasks as a sibling module, so its folder goes on the
# path first. It is loaded under its own name because this file is also
# called run.py.
sys.path.insert(0, str(LOCALIZATION))
_spec = importlib.util.spec_from_file_location("localization_run", LOCALIZATION / "run.py")
loc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(loc)

FIND_LIMIT = 20
SUGGESTED = 10
ARMS = ("off", "on")
# Files that are not useful retrieval targets. A commit whose existing
# gold files are all of these is skipped.
SKIP_NAMES = {"CHANGELOG.md", "uv.lock", "package-lock.json", "poetry.lock"}
PR_SUFFIX = re.compile(r"\s+\(#\d+\)$")


def query_of(subject: str) -> str:
    return PR_SUFFIX.sub("", subject).strip()


def git(repo: Path, *args: str) -> str:
    return loc.run(["git", *args], repo)


def split_z(output: str) -> list[str]:
    return [item.strip("\n") for item in output.split("\0") if item.strip("\n")]


def clear(path: Path) -> None:
    """Remove a tree, including read-only git objects on Windows."""
    if not path.exists():
        return

    def make_writable(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=make_writable)
    else:
        shutil.rmtree(path, onerror=make_writable)


def clone_head(repo: Path, dest: Path) -> Path:
    """Clone the repository at HEAD, so no mimry file lands in --repo.

    Each arm gets its own clone, because a MIMRY index is tied to the
    repository it was built in, and that repository records its cache.
    """
    sha = git(repo, "rev-parse", "HEAD").strip()
    clear(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    git(dest.parent, "clone", "-q", "--no-checkout", str(repo), str(dest))
    git(dest, "checkout", "-q", "--detach", sha)
    return dest


def commits_to_replay(head: Path, count: int) -> list[str]:
    """The last `count` non-merge commits from HEAD, oldest first."""
    shas = git(head, "log", "--no-merges", "--format=%H", "-n", str(count)).split()
    return list(reversed(shas))


def changed_files(head: Path, sha: str) -> list[str]:
    output = git(head, "show", "--name-only", "--diff-filter=AM", "--format=", "-z", sha)
    return split_z(output)


def plan_tasks(head: Path, count: int) -> tuple[list[dict], Counter]:
    at_head = set(split_z(git(head, "ls-tree", "-r", "--name-only", "-z", "HEAD")))
    tasks: list[dict] = []
    skipped: Counter = Counter()
    for sha in commits_to_replay(head, count):
        subject = git(head, "log", "-1", "--format=%s", sha).strip()
        query = query_of(subject)
        if not query:
            skipped["empty_subject"] += 1
            continue
        existing = [path for path in changed_files(head, sha) if path in at_head]
        if not existing:
            skipped["no_gold_at_head"] += 1
            continue
        gold = sorted(path for path in existing if Path(path).name not in SKIP_NAMES)
        if not gold:
            skipped["changelog_or_lock_only"] += 1
            continue
        tasks.append({"sha": sha, "subject": subject, "query": query, "gold": gold})
    return tasks, skipped


def index_arm(root: Path, cache: Path) -> dict[str, str]:
    """Index the clone at HEAD into an empty cache of its own."""
    clear(cache)
    env = {**os.environ, "MIMRY_CACHE_HOME": str(cache), "PYTHONIOENCODING": "utf-8"}
    if not (root / ".mimry" / "config.toml").exists():
        loc.mimry(root, env, "init", "--skip-graph")
    loc.mimry(root, env, "index")
    return env


def _key(rank: int | None) -> float:
    return math.inf if rank is None else rank


def score_arm(ranked: list[str], gold: list[str]) -> dict:
    """Score one ranked list against the gold files.

    gold_ranks maps each gold file to its 1-based rank, or None when it
    is not in the list. reciprocal_rank is 0 when no gold file is in it.
    """
    if not gold:
        raise ValueError("score_arm needs at least one gold file")
    unique = list(dict.fromkeys(ranked))[:FIND_LIMIT]
    position = {path: rank for rank, path in enumerate(unique, 1)}
    gold_set = sorted(set(gold))
    gold_ranks = {path: position.get(path) for path in gold_set}
    found = sorted(rank for rank in gold_ranks.values() if rank is not None)

    def hit(k: int) -> bool:
        return bool(found) and found[0] <= k

    return {
        "ranked": unique,
        "gold_ranks": gold_ranks,
        "hit_at_1": hit(1),
        "hit_at_5": hit(5),
        "hit_at_10": hit(10),
        "recall_at_10": sum(rank <= 10 for rank in found) / len(gold_set),
        "reciprocal_rank": 1 / found[0] if found else 0.0,
    }


def compare(off: dict, on: dict) -> dict:
    """Compare the two arms' gold ranks for one task.

    helped: some gold file ranks higher with feedback, and none lower.
    hurt: some gold file ranks lower with feedback.
    pushed_out_of_top5: gold files in the top 5 without feedback that
    are not in the top 5 with it. A gold file outside the list ranks as
    infinity.
    """
    helped_any = False
    hurt_any = False
    pushed = []
    for path, off_rank in off["gold_ranks"].items():
        on_rank = on["gold_ranks"].get(path)
        before, after = _key(off_rank), _key(on_rank)
        helped_any = helped_any or after < before
        hurt_any = hurt_any or after > before
        if off_rank is not None and off_rank <= 5 and (on_rank is None or on_rank > 5):
            pushed.append({"path": path, "off_rank": off_rank, "on_rank": on_rank})
    return {
        "helped": helped_any and not hurt_any,
        "hurt": hurt_any,
        "pushed_out_of_top5": pushed,
    }


def record(
    head: Path, env: dict[str, str], task: dict, suggested: list[str], payload: Path
) -> None:
    """Record the task the way commit feedback does, through the CLI."""
    data = {
        "query": task["query"],
        "suggested": suggested,
        "changed": task["gold"],
        "opened": [],
        "ignored": [],
        "outcome": "unknown",
        "notes": f"Recorded from commit {task['sha'][:12]}",
    }
    payload.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8", newline="\n")
    loc.mimry(head, env, "feedback", "--json", str(payload))


def replay(
    roots: dict[str, Path], envs: dict[str, dict[str, str]], tasks: list[dict], work: Path
) -> list[dict]:
    payload = work / "feedback-payload.json"
    seen_gold: set[str] = set()
    rows: list[dict] = []
    for position, task in enumerate(tasks, 1):
        row = {
            "sha": task["sha"],
            "subject": task["subject"],
            "query": task["query"],
            "gold": task["gold"],
        }
        try:
            for arm in ARMS:
                env = envs[arm]
                root = roots[arm]
                out = loc.mimry(root, env, "find", "--limit", str(FIND_LIMIT), "--", task["query"])
                ranked = loc.parse_find(out, depth=FIND_LIMIT)
                row[arm] = score_arm(ranked, task["gold"])
                if arm == "on":
                    record(root, env, task, ranked[:SUGGESTED], payload)
        except RuntimeError as exc:
            row |= {"status": "error", "error": str(exc)[-500:]}
        else:
            row |= {
                "status": "scored",
                "comparison": compare(row["off"], row["on"]),
                "with_history": bool(seen_gold & set(task["gold"])),
            }
            seen_gold |= set(task["gold"])
        rows.append(row)
        detail = row.get("error") or (f"off {_first(row['off'])} on {_first(row['on'])}")
        print(
            f"{position}/{len(tasks)} {task['sha'][:12]} {row['status']:<7} {detail}",
            file=sys.stderr,
        )
    return rows


def _first(arm: dict) -> str:
    rank = min((r for r in arm["gold_ranks"].values() if r is not None), default=None)
    return f"first {rank}" if rank is not None else "miss"


def pct(values: list) -> float | None:
    return round(100 * sum(values) / len(values), 1) if values else None


def summarize_arm(rows: list[dict], arm: str) -> dict:
    return {
        "tasks": len(rows),
        "hit_at_1": pct([row[arm]["hit_at_1"] for row in rows]),
        "hit_at_5": pct([row[arm]["hit_at_5"] for row in rows]),
        "hit_at_10": pct([row[arm]["hit_at_10"] for row in rows]),
        "recall_at_10": pct([row[arm]["recall_at_10"] for row in rows]),
        "mrr": pct([row[arm]["reciprocal_rank"] for row in rows]),
    }


def summarize(rows: list[dict]) -> dict:
    pushed = [
        {"sha": row["sha"], **item}
        for row in rows
        for item in row["comparison"]["pushed_out_of_top5"]
    ]
    return {
        "tasks": len(rows),
        "off": summarize_arm(rows, "off"),
        "on": summarize_arm(rows, "on"),
        "helped": sum(row["comparison"]["helped"] for row in rows),
        "hurt": sum(row["comparison"]["hurt"] for row in rows),
        "pushed_out_of_top5": {"count": len(pushed), "list": pushed},
    }


def render_table(summary: dict) -> str:
    def cell(value: float | None) -> str:
        return "-" if value is None else f"{value:.1f}"

    lines = [
        "| Group | Arm | Tasks | hit@1 | hit@5 | hit@10 | recall@10 | MRR |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    notes = []
    for group, label in (("all", "all"), ("with_history", "with history")):
        result = summary[group]
        for arm in ARMS:
            stats = result[arm]
            lines.append(
                f"| {label} | {arm} | {stats['tasks']} | {cell(stats['hit_at_1'])}"
                f" | {cell(stats['hit_at_5'])} | {cell(stats['hit_at_10'])}"
                f" | {cell(stats['recall_at_10'])} | {cell(stats['mrr'])} |"
            )
        notes.append(
            f"{label}: helped {result['helped']}, hurt {result['hurt']},"
            f" pushed out of top 5 {result['pushed_out_of_top5']['count']}"
        )
    skipped = ", ".join(
        f"{count} {reason}" for reason, count in sorted(summary["skipped"].items())
    )
    lines.append("")
    lines.extend(notes)
    lines.append(
        f"Replayed {summary['scored']} tasks, {summary['errors']} errors,"
        f" skipped {sum(summary['skipped'].values())} commits ({skipped or 'none'})."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--repo", type=Path, required=True, help="Git repository to replay (only read)"
    )
    parser.add_argument(
        "--commits",
        type=int,
        default=200,
        help="Non-merge commits to take from HEAD, before skips",
    )
    parser.add_argument(
        "--work",
        type=Path,
        required=True,
        help="Scratch directory for the HEAD clone and the caches",
    )
    parser.add_argument("--out", type=Path, required=True, help="Results JSON file")
    parser.add_argument(
        "--summary-file",
        type=Path,
        help="Also append the Markdown table here, e.g. $GITHUB_STEP_SUMMARY",
    )
    args = parser.parse_args(argv)
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    repo = args.repo.resolve()
    roots = {arm: clone_head(repo, work / "repos" / arm) for arm in ARMS}
    tasks, skipped = plan_tasks(roots["off"], args.commits)
    print(f"{len(tasks)} tasks, {sum(skipped.values())} commits skipped", file=sys.stderr)
    envs = {arm: index_arm(roots[arm], work / "cache" / arm) for arm in ARMS}
    rows = replay(roots, envs, tasks, work)
    scored = [row for row in rows if row["status"] == "scored"]
    history = [row for row in scored if row["with_history"]]
    summary = {
        "repo": str(args.repo.resolve()),
        "commits": args.commits,
        "scored": len(scored),
        "errors": len(rows) - len(scored),
        "skipped": dict(skipped),
        "all": summarize(scored),
        "with_history": summarize(history),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps({"summary": summary, "tasks": rows}, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    table = render_table(summary)
    print(table)
    if args.summary_file:
        with args.summary_file.open("a", encoding="utf-8") as handle:
            handle.write(f"## Feedback replay: {summary['repo']}\n\n{table}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
