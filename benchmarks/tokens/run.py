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

With --reads sed, a read is what `grep -n` and `sed -n` show instead:
the agent pays for `git grep -n` of the task keywords in that file, then
reads --window lines (default 100) on each side of each matching line.
The MIMRY agent anchors on the symbol lines its pack lists for that file
instead, when there are any. A file with no anchor is read from the top.
A gold file counts as reached only when a window shows a line the pull
request changed. For a rebase-merged pull request whose earlier commits
are already in the base, a gold file the last commit does not touch
counts as reached by any read, as in full mode. Results go to
results.<set>.sed.json.

Tokens are counted with tiktoken's o200k_base encoding. Run from the
repo root:

    uv run --with tiktoken python benchmarks/tokens/run.py \\
        --work C:/tmp/mimry-tokens \\
        [--set dev|holdout|holdout2] [--reads full|sed]
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
SYMBOL_LINE = re.compile(r"^- `[^`]+` \([^)]*\) - `([^`]+):(\d+)`", re.M)
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


def changed_lines(repo: Path, task: dict) -> dict[str, set[int]]:
    """Changed lines per gold file; absent if rebase merge untouched."""
    base, gold = task["base"], set(task["gold"])
    log = run(["git", "log", "--all", "--format=%H%x00%P%x00%s"], repo)
    children = []
    for line in log.splitlines():
        sha, parents, subject = line.split("\0", 2)
        if parents.split()[:1] == [base]:
            children.append((sha, subject))
    named = [sha for sha, subject in children if re.search(rf"#{task['pr']}\b", subject)]
    touching = [
        sha
        for sha, _ in children
        if gold <= set(run(["git", "diff", "--name-only", base, sha], repo).splitlines())
    ]
    merge = (named or touching or [sha for sha, _ in children] or [None])[0]
    if merge is None:
        raise RuntimeError(f"{task['id']}: no commit after {base}")
    lines: dict[str, set[int]] = {}
    for rel in gold:
        diff = run(["git", "diff", "-U0", base, merge, "--", rel], repo)
        hits: set[int] = set()
        for start, count in re.findall(r"^@@ -(\d+)(?:,(\d+))? \+", diff, flags=re.M):
            start, count = int(start), int(count or 1)
            # A pure insertion (count 0) sits after line `start`.
            hits.update(range(start, start + count) if count else (start, start + 1))
        hits.discard(0)
        if hits:
            lines[rel] = hits
    return lines


def keyword_lines(repo: Path, rel: str, words: list[str]) -> tuple[int, list[int]]:
    """What `grep -n` shows for the task's keywords in one file."""
    if not words:
        return 0, []
    pattern = [arg for word in words for arg in ("-e", word)]
    out = run(["git", "grep", "-n", "-i", "-I", "-F", *pattern, "--", rel], repo)
    shown = out.splitlines()[:GREP_OUTPUT_LINES]
    return tokens("\n".join(shown)), [int(line.split(":", 2)[1]) for line in shown]


def window_read(
    repo: Path, rel: str, anchors: list[int], changed: set[int] | None, width: int
) -> tuple[int, bool]:
    """Cost of sed windows around anchors; whether one shows change."""
    try:
        lines = (repo / rel).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0, False
    spans: list[list[int]] = []
    for anchor in sorted(anchors or [1 + width]):
        start, end = max(1, anchor - width), min(len(lines), anchor + width)
        if spans and start <= spans[-1][1] + 1:
            spans[-1][1] = max(spans[-1][1], end)
        else:
            spans.append([start, end])
    text = "\n".join("\n".join(lines[start - 1 : end]) for start, end in spans)
    shown = changed is None or any(
        start <= line <= end for start, end in spans for line in changed
    )
    return tokens(text), shown


def sed_read(
    repo: Path, path: str, sed: dict, anchors: list[int] | None = None
) -> tuple[int, bool]:
    cost = 0
    if not anchors:
        cost, anchors = keyword_lines(repo, path, sed["words"])
    spent, shown = window_read(repo, path, anchors, sed["changed"].get(path), sed["width"])
    return cost + spent, shown


def walk(
    repo: Path, gold: list[str], search_cost: int, order: list[str], fallback=None, read=None
) -> dict[str, object]:
    custom_read = read is not None
    read = read or (lambda path: (read_cost(repo, path), True))
    remaining, opened, cost, first_gold, missed = set(gold), [], search_cost, None, 0
    queue = list(order)
    while remaining and len(opened) < READ_BUDGET:
        if not queue:
            if fallback is None:
                break
            extra, more = fallback()
            fallback, cost = None, cost + extra
            queue = [p for p in more if p not in opened]
            continue
        path = queue.pop(0)
        if path in opened:
            continue
        opened.append(path)
        spent, shown = read(path)
        cost += spent
        if path in remaining:
            if shown:
                remaining.discard(path)
                first_gold = first_gold or len(opened)
            else:
                missed += 1
    result = {
        "tokens": cost,
        "search_tokens": search_cost,
        "reads": len(opened),
        "first_gold_read": first_gold,
        "gold_found": len(gold) - len(remaining),
        "success": not remaining,
    }
    if custom_read:
        result["gold_missed"] = missed
    return result


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


def mimry_agent(
    repo: Path, task: dict, env: dict[str, str], sed: dict | None = None
) -> dict[str, object]:
    cli = [sys.executable, "-m", "mimry.cli", "--root", str(repo)]
    run([*cli, "index"], repo, env)
    printed = run([*cli, "preflight", task["task"]], repo, env)
    pack = (repo / ".mimry" / "mimry-out" / "context" / "latest.md").read_text(encoding="utf-8")
    order = re.findall(r"^### \d+\. `([^`]+)`", pack, flags=re.M)
    symbols: dict[str, list[int]] = {}
    if sed is not None:
        for rel, line in SYMBOL_LINE.findall(pack):
            symbols.setdefault(rel, []).append(int(line))
    read = None if sed is None else (lambda path: sed_read(repo, path, sed, symbols.get(path)))
    return walk(
        repo,
        task["gold"],
        tokens(printed) + tokens(pack),
        order,
        lambda: grep_search(repo, task["task"]),
        read=read,
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
        # Skip rows where grep found nothing; no per-task ratio.
        "median_task_savings_vs_grep_pct": round(
            100
            * statistics.median(
                1 - r["mimry"]["tokens"] / r["grep"]["tokens"]
                for r in results
                if r["grep"]["tokens"] > 0
            ),
            1,
        ),
        "median_x_fewer_than_reading_whole_repo": round(statistics.median(ratios), 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--work", type=Path, required=True, help="Scratch directory for clones and the MIMRY cache"
    )
    parser.add_argument(
        "--set", default="dev", choices=["dev", "holdout", "holdout2"], help="Task set to run"
    )
    parser.add_argument("--only", help="Run only task ids containing this text")
    parser.add_argument(
        "--reads",
        default="full",
        choices=["full", "sed"],
        help="full: a read is the file's first 2,000 lines; sed: windows around anchor lines",
    )
    parser.add_argument(
        "--window", type=int, default=100, help="sed mode: lines read on each side of an anchor"
    )
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
        sed = None
        if args.reads == "sed":
            sed = {
                "changed": changed_lines(repo, task),
                "width": args.window,
                "words": keywords(task["task"]),
            }
        row = {
            "id": task["id"],
            "task": task["task"],
            "gold": task["gold"],
            "repo_tokens": repo_tokens[key],
            "grep": walk(
                repo,
                task["gold"],
                cost,
                ranked,
                read=sed and (lambda path, repo=repo, sed=sed: sed_read(repo, path, sed)),
            ),
            "mimry": mimry_agent(repo, task, env, sed),
        }
        results.append(row)
        g, m = row["grep"], row["mimry"]
        print(
            f"{task['id']:<28} grep {g['tokens']:>7} {'ok' if g['success'] else '--'}"
            f"   mimry {m['tokens']:>7} {'ok' if m['success'] else '--'}",
            file=sys.stderr,
        )

    summary = summarize(results)
    if args.reads == "sed":
        summary |= {"reads": "sed", "window": args.window}
    report = {"summary": summary, "tasks": results}
    if not args.only:
        suffix = ".sed" if args.reads == "sed" else ""
        (HERE / f"results.{args.set}{suffix}.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(report["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
