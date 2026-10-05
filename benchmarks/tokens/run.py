"""Measure how many tokens an agent reads before it reaches the files a
task changes.

Two scripted agents get the same task text (the PR title) and the same
read budget:

- grep agent: extracts keywords from the task, runs one `git grep -c`
  per keyword (the output it would see), ranks files by how many
  distinct keywords they match and then by match count, and reads files
  in that order
- MIMRY agent: runs `mimry preflight`, reads the printed summary and the
  context pack, reads files in the pack's order, then falls back to the
  grep agent's search (paying for it) if the pack runs out

Both stop once every gold file has been read, or after READ_BUDGET
reads. A read costs the tokens of the file's first 2,000 lines, like a
coding agent's file tool. Indexing is not charged: it is local,
one-time, and uses no model tokens.

Tokens are counted with tiktoken's o200k_base encoding. Run from the
repo root:

    uv run --with tiktoken python benchmarks/tokens/run.py \\
        --work C:/tmp/mimry-tokens [--set dev|holdout]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
from pathlib import Path

import tiktoken

HERE = Path(__file__).resolve().parent
READ_BUDGET = 10
READ_LINES = 2000
GREP_OUTPUT_LINES = 250
STOPWORDS = set(
    """a an and are as at be by can case cases change changes despite directly do
    does feat fix fixes for from has have in instead into is it its make more not
    of off on or perf potential refactor several some that the their this to
    update updating use uses using via when with without add adds allow support
    handle remove
    """.split()
)
ENC = tiktoken.get_encoding("o200k_base")


def tokens(text: str) -> int:
    return len(ENC.encode(text, disallowed_special=()))


def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> str:
    out = subprocess.run(
        cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if out.returncode not in (0, 1):  # git grep exits 1 when nothing matches
        raise RuntimeError(f"{cmd} failed ({out.returncode}): {out.stderr.strip()}")
    return out.stdout


def keywords(task: str) -> list[str]:
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", task)
    seen: dict[str, None] = {}
    for word in words:
        key = word.lower()
        if len(key) >= 3 and key not in STOPWORDS:
            seen.setdefault(key)
    return list(seen)


def read_cost(repo: Path, rel: str) -> int:
    try:
        text = (repo / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return tokens("\n".join(text.splitlines()[:READ_LINES]))


def grep_search(repo: Path, task: str) -> tuple[int, list[str]]:
    cost, hits, counts = 0, {}, {}
    for word in keywords(task):
        out = run(["git", "grep", "-c", "-i", "-I", "-F", word], repo)
        lines = out.splitlines()[:GREP_OUTPUT_LINES]
        cost += tokens("\n".join(lines))
        for line in lines:
            path, _, count = line.rpartition(":")
            hits[path] = hits.get(path, 0) + 1
            counts[path] = counts.get(path, 0) + int(count or 0)
    ranked = sorted(hits, key=lambda p: (-hits[p], -counts[p], p))
    return cost, ranked


def walk(
    repo: Path, gold: list[str], search_cost: int, order: list[str], fallback=None
) -> dict[str, object]:
    remaining, read, cost, first_gold = set(gold), [], search_cost, None
    queue = list(order)
    while remaining and len(read) < READ_BUDGET:
        if not queue:
            if fallback is None:
                break
            extra, more = fallback()
            fallback, cost = None, cost + extra
            queue = [p for p in more if p not in read]
            continue
        path = queue.pop(0)
        if path in read:
            continue
        read.append(path)
        cost += read_cost(repo, path)
        if path in remaining:
            remaining.discard(path)
            first_gold = first_gold or len(read)
    return {
        "tokens": cost,
        "search_tokens": search_cost,
        "reads": len(read),
        "first_gold_read": first_gold,
        "gold_found": len(gold) - len(remaining),
        "success": not remaining,
    }


def whole_repo_tokens(repo: Path) -> int:
    files = run(["git", "ls-files"], repo).splitlines()
    total = 0
    for rel in files:
        try:
            total += tokens((repo / rel).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
    return total


def checkout(work: Path, repo_name: str, sha: str) -> Path:
    repo = work / "repos" / repo_name.replace("/", "__")
    if not repo.exists():
        repo.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "-q", f"https://github.com/{repo_name}.git", str(repo)], work)
    run(["git", "checkout", "-q", "-f", "--detach", sha], repo)
    run(["git", "clean", "-q", "-fdx", "-e", ".mimry"], repo)
    return repo


def mimry_agent(repo: Path, task: dict, env: dict[str, str]) -> dict[str, object]:
    cli = [sys.executable, "-m", "mimry.cli", "--root", str(repo)]
    run([*cli, "index"], repo, env)
    printed = run([*cli, "preflight", task["task"]], repo, env)
    pack = (repo / ".mimry" / "mimry-out" / "context" / "latest.md").read_text(encoding="utf-8")
    order = re.findall(r"^### \d+\. `([^`]+)`", pack, flags=re.M)
    return walk(
        repo,
        task["gold"],
        tokens(printed) + tokens(pack),
        order,
        lambda: grep_search(repo, task["task"]),
    )


def summarize(results: list[dict]) -> dict[str, object]:
    def total(agent: str, rows: list[dict]) -> int:
        return sum(r[agent]["tokens"] for r in rows)

    both = [r for r in results if r["grep"]["success"] and r["mimry"]["success"]]
    ratios = [r["repo_tokens"] / r["mimry"]["tokens"] for r in results]
    return {
        "tasks": len(results),
        "read_budget": READ_BUDGET,
        "success": {a: sum(r[a]["success"] for r in results) for a in ("grep", "mimry")},
        "tokens_total": {a: total(a, results) for a in ("grep", "mimry")},
        "savings_vs_grep_pct": round(
            100 * (1 - total("mimry", results) / total("grep", results)), 1
        ),
        "both_succeeded": len(both),
        "savings_vs_grep_pct_both_succeeded": round(
            100 * (1 - total("mimry", both) / total("grep", both)), 1
        )
        if both
        else None,
        "median_task_savings_vs_grep_pct": round(
            100
            * statistics.median(1 - r["mimry"]["tokens"] / r["grep"]["tokens"] for r in results),
            1,
        ),
        "median_x_fewer_than_reading_whole_repo": round(statistics.median(ratios), 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--work", type=Path, required=True, help="Scratch directory for clones and the MIMRY cache"
    )
    parser.add_argument("--set", default="dev", choices=["dev", "holdout"], help="Task set to run")
    parser.add_argument("--only", help="Run only task ids containing this text")
    args = parser.parse_args()
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "MIMRY_CACHE_HOME": str(work / "cache"), "PYTHONIOENCODING": "utf-8"}

    tasks = json.loads((HERE / f"tasks.{args.set}.json").read_text(encoding="utf-8"))
    if args.only:
        tasks = [t for t in tasks if args.only in t["id"]]
    results, repo_tokens = [], {}
    for task in tasks:
        repo = checkout(work, task["repo"], task["base"])
        key = (task["repo"], task["base"])
        repo_tokens[key] = repo_tokens.get(key) or whole_repo_tokens(repo)
        cost, ranked = grep_search(repo, task["task"])
        row = {
            "id": task["id"],
            "task": task["task"],
            "gold": task["gold"],
            "repo_tokens": repo_tokens[key],
            "grep": walk(repo, task["gold"], cost, ranked),
            "mimry": mimry_agent(repo, task, env),
        }
        results.append(row)
        g, m = row["grep"], row["mimry"]
        print(
            f"{task['id']:<28} grep {g['tokens']:>7} {'ok' if g['success'] else '--'}"
            f"   mimry {m['tokens']:>7} {'ok' if m['success'] else '--'}",
            file=sys.stderr,
        )

    report = {"summary": summarize(results), "tasks": results}
    if not args.only:
        (HERE / f"results.{args.set}.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(report["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
