"""Build tasks.verified.json with each instance's gold files.

The rows come from the Hugging Face datasets-server JSON API. Only the
fields the runner needs are kept. Neither the issue text nor the patch
is stored: run.py fetches the issue text at run time, and the patch is
read here once to list the files it modifies or deletes at the base
commit.

Run from the repo root:

    uv run python benchmarks/localization/build_tasks.py
"""

from __future__ import annotations

import itertools
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

DATASET = "princeton-nlp/SWE-bench_Verified"
API = "https://datasets-server.huggingface.co/rows"
EXPECTED_ROWS = 500
PAGE = 100
ATTEMPTS = 3
OUT = Path(__file__).resolve().parent / "tasks.verified.json"


def _get_json(url: str) -> dict:
    last: Exception | None = None
    for attempt in range(ATTEMPTS):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                return json.load(response)
        except OSError as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{url} failed after {ATTEMPTS} attempts: {last}")


def fetch_rows(dataset: str = DATASET) -> list[dict]:
    """Every row of the dataset's test split, in dataset order."""
    rows: list[dict] = []
    total: int | None = None
    while total is None or len(rows) < total:
        query = urllib.parse.urlencode(
            {
                "dataset": dataset,
                "config": "default",
                "split": "test",
                "offset": len(rows),
                "length": PAGE,
            }
        )
        page = _get_json(f"{API}?{query}")
        if page.get("partial"):
            raise RuntimeError(f"{dataset}: the API returned a partial page")
        total = page["num_rows_total"]
        if not page["rows"]:
            raise RuntimeError(f"{dataset}: empty page at offset {len(rows)}")
        for item in page["rows"]:
            if item["truncated_cells"]:
                raise RuntimeError(f"{item['row']['instance_id']}: truncated cells")
            rows.append(item["row"])
    if len(rows) != EXPECTED_ROWS or total != EXPECTED_ROWS:
        raise RuntimeError(f"{dataset}: expected {EXPECTED_ROWS} rows, got {len(rows)}")
    return rows


def header_paths(line: str) -> tuple[str, str]:
    """Return (a, b) from a "diff --git a/X b/Y" line.

    The two names are equal unless the file was renamed or copied. A
    name may itself contain " b/", so an unchanged path is matched by
    halves before falling back to the first separator.
    """
    rest = line[len("diff --git ") :]
    if not rest.startswith("a/") or " b/" not in rest:
        raise ValueError(f"unsupported diff header: {line!r}")
    rest = rest[2:]
    half, odd = divmod(len(rest) - 3, 2)
    if not odd and rest[half : half + 3] == " b/" and rest[:half] == rest[half + 3 :]:
        return rest[:half], rest[half + 3 :]
    old, _, new = rest.partition(" b/")
    return old, new


def parse_patch(patch: str) -> tuple[list[str], int]:
    """Return the files a patch changes at its base, and the add count.

    The first value is sorted. Added files do not exist at the base
    commit, so they are counted, not listed. A rename lists the old
    path, which is the file that exists at the base.
    """
    gold: set[str] = set()
    added = 0
    for block in re.split(r"(?m)^(?=diff --git )", patch):
        if not block.startswith("diff --git "):
            continue
        # The header is everything before the first hunk.
        head = list(
            itertools.takewhile(lambda line: not line.startswith("@@"), block.splitlines())
        )
        old, _ = header_paths(head[0])
        if any(line.startswith(("new file mode", "copy from")) for line in head) or (
            "--- /dev/null" in head
        ):
            added += 1
            continue
        renamed_from = [
            line[len("rename from ") :] for line in head if line.startswith("rename from ")
        ]
        gold.add(renamed_from[0] if renamed_from else old)
    return sorted(gold), added


def build(rows: list[dict]) -> list[dict]:
    tasks = []
    for row in rows:
        gold, added = parse_patch(row["patch"])
        tasks.append(
            {
                "instance_id": row["instance_id"],
                "repo": row["repo"],
                "base_commit": row["base_commit"],
                "gold": gold,
                "added": added,
            }
        )
    tasks.sort(key=lambda task: task["instance_id"])
    if len({task["instance_id"] for task in tasks}) != EXPECTED_ROWS:
        raise RuntimeError("duplicate or missing instance ids")
    return tasks


def main() -> int:
    tasks = build(fetch_rows())
    OUT.write_text(
        json.dumps(tasks, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    empty = sum(1 for task in tasks if not task["gold"])
    print(f"wrote {len(tasks)} rows to {OUT} ({empty} with no gold files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
