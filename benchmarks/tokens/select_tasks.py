"""Select benchmark tasks from real merged pull requests.

The rule is mechanical and fixed before any result is seen, so tasks
cannot be cherry-picked for MIMRY:

- newest merged PRs first, scanning at most SCAN_LIMIT per repository
- skip bot authors and housekeeping titles (bump, release, revert, docs,
  ...), plus tooling-only titles (lint, formatter, codespell, cleanup)
  whose diffs touch files no task description could point to
- the task text is the PR title only; the body often names the files
  outright
- the gold set is the non-test source files the PR *modified* (added
  files cannot be found before they exist), between 1 and 4 of them
- the task runs at the first parent of the merge commit, the code as it
  was before the change

Requires an authenticated `gh` CLI. Usage: select_tasks.py
[dev|holdout|holdout2]. Writes benchmarks/tokens/tasks.<set>.json.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCAN_LIMIT = 300
SOURCE_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".cs", ".java", ".php"}
SKIP_TITLE = re.compile(
    r"^\W*(bump|chore|release|revert|ci|docs?|build|deps|test|tests|style|typo|merge|update"
    r" changelog)\b"
    r"|dependabot|renovate|\bchangelog\b|\btypos?\b|\bversion\b"
    r"|\blint(er)?\b|\bformatter\b|codespell|pre-commit|\bcleanup\b",
    re.IGNORECASE,
)
TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|spec|testdata|fixtures?|benches|examples?)/"
    r"|(^|/)test_[^/]*$|_test\.(go|py)$|\.(test|spec)\.[jt]sx?$|Tests?\.(cs|java)$"
)


def gh(path: str) -> object:
    out = subprocess.run(
        ["gh", "api", path], check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout
    return json.loads(out)


def is_source(path: str) -> bool:
    return Path(path).suffix in SOURCE_EXTENSIONS and not TEST_PATH.search(path)


def select(repo: str, per_repo: int) -> list[dict[str, object]]:
    tasks: list[dict[str, object]] = []
    for page in range(1, SCAN_LIMIT // 100 + 1):
        pulls = gh(
            f"repos/{repo}/pulls?state=closed&sort=created&direction=desc&per_page=100&page={page}"
        )
        for pr in pulls:
            if len(tasks) == per_repo:
                return tasks
            title = pr["title"].strip()
            if not pr.get("merged_at") or not pr.get("merge_commit_sha"):
                continue
            if pr["user"]["type"] == "Bot" or pr["user"]["login"].endswith("[bot]"):
                continue
            if SKIP_TITLE.search(title) or len(re.findall(r"[A-Za-z]{2,}", title)) < 3:
                continue
            files = gh(f"repos/{repo}/pulls/{pr['number']}/files?per_page=100")
            gold = sorted(
                f["filename"]
                for f in files
                if f["status"] == "modified" and is_source(f["filename"])
            )
            if not 1 <= len(gold) <= 4:
                continue
            merge = gh(f"repos/{repo}/commits/{pr['merge_commit_sha']}")
            tasks.append(
                {
                    "id": f"{repo}#{pr['number']}",
                    "repo": repo,
                    "pr": pr["number"],
                    "task": title,
                    "base": merge["parents"][0]["sha"],
                    "gold": gold,
                }
            )
    return tasks


def main() -> int:
    # "dev" tasks may be inspected while improving ranking; "holdout"
    # tasks come from different repositories and are only run to report
    # the final number.
    name = sys.argv[1] if len(sys.argv) > 1 else "dev"
    config = json.loads((HERE / f"repos.{name}.json").read_text(encoding="utf-8"))
    tasks = [task for repo in config["repos"] for task in select(repo, config["tasks_per_repo"])]
    (HERE / f"tasks.{name}.json").write_text(json.dumps(tasks, indent=2) + "\n", encoding="utf-8")
    print(f"selected {len(tasks)} tasks", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
