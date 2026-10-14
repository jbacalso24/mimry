"""Build tasks.<set>.json with each instance's gold files.

There are two sets.
The verified set is the SWE-bench Verified test split, 500 rows.
The dev set is a fixed sample of 300 instances from the full SWE-bench
test split that are not in Verified. Ranking changes are tuned on dev,
and Verified is run once per frozen change.

The dev selection rule is fixed in select_dev, before any result is
seen: drop every instance id that is in Verified, sort the rest by the
hex sha256 digest of the id, and keep the first 300.

The rows come from the Hugging Face datasets-server JSON API. Only the
fields the runner needs are kept. Neither the issue text nor the patch
is stored: run.py fetches the issue text at run time, and the patch is
read here once to list the files it modifies or deletes at the base
commit.

Run from the repo root:

    uv run python benchmarks/localization/build_tasks.py
    uv run python benchmarks/localization/build_tasks.py --set dev
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = "https://datasets-server.huggingface.co/rows"
PAGE = 100
ATTEMPTS = 6
DEV_SIZE = 300
# The dataset each set is built from, and the exact row count of its
# test split. The dev set is drawn from the full SWE-bench test split.
SETS = {
    "verified": {"dataset": "princeton-nlp/SWE-bench_Verified", "rows": 500},
    "dev": {"dataset": "princeton-nlp/SWE-bench", "rows": 2294},
}


def task_path(set_name: str) -> Path:
    return HERE / f"tasks.{set_name}.json"


def _get_json(url: str) -> dict:
    last: Exception | None = None
    for attempt in range(ATTEMPTS):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                return json.load(response)
        except OSError as exc:
            last = exc
            # Ten CI shards fetch at once and the API rate-limits bursts
            # (429), so wait longer each time, or as long as it asks.
            retry_after = getattr(exc, "headers", None) and exc.headers.get("Retry-After")
            wait = int(retry_after) if str(retry_after or "").isdigit() else 5 * 2**attempt
            time.sleep(min(wait, 120))
    raise RuntimeError(f"{url} failed after {ATTEMPTS} attempts: {last}")


def fetch_rows(dataset: str, expected: int) -> list[dict]:
    """Every row of the dataset's test split, in dataset order.

    Fails unless the split holds exactly `expected` rows. A truncated
    cell fails too, so no patch is ever read partially.
    """
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
    if len(rows) != expected or total != expected:
        raise RuntimeError(f"{dataset}: expected {expected} rows, got {len(rows)}")
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
    if len({task["instance_id"] for task in tasks}) != len(tasks):
        raise RuntimeError("duplicate instance ids")
    return tasks


def select_dev(instance_ids: list[str], verified_ids: set[str], size: int = DEV_SIZE) -> list[str]:
    """The dev instance ids, in sha256 order, at most `size` of them.

    The rule is fixed and mechanical: drop every id that is in Verified,
    sort the rest by the hex sha256 digest of the id (UTF-8), and keep
    the first `size`. Nothing about an instance's content or its result
    takes part.
    """
    candidates = [
        instance_id
        for instance_id in dict.fromkeys(instance_ids)
        if instance_id not in verified_ids
    ]
    candidates.sort(
        key=lambda instance_id: hashlib.sha256(instance_id.encode("utf-8")).hexdigest()
    )
    return candidates[:size]


def build_dev(full_rows: list[dict]) -> list[dict]:
    """The dev tasks, drawn from the full split rows fetched for it."""
    verified = {
        task["instance_id"]
        for task in json.loads(task_path("verified").read_text(encoding="utf-8"))
    }
    ids = [row["instance_id"] for row in full_rows]
    missing = verified - set(ids)
    if missing:
        raise RuntimeError(
            f"{len(missing)} Verified ids are not in the full split, e.g. {sorted(missing)[0]}"
        )
    chosen = select_dev(ids, verified)
    if len(chosen) != DEV_SIZE:
        raise RuntimeError(f"dev: {len(chosen)} eligible instances, expected {DEV_SIZE}")
    by_id = {row["instance_id"]: row for row in full_rows}
    return build([by_id[instance_id] for instance_id in chosen])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--set",
        dest="set_name",
        choices=sorted(SETS),
        default="verified",
        help="Which task set to build (default: verified)",
    )
    args = parser.parse_args(argv)
    spec = SETS[args.set_name]
    rows = fetch_rows(spec["dataset"], spec["rows"])
    tasks = build_dev(rows) if args.set_name == "dev" else build(rows)
    out = task_path(args.set_name)
    out.write_text(
        json.dumps(tasks, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    empty = sum(1 for task in tasks if not task["gold"])
    print(f"wrote {len(tasks)} rows to {out} ({empty} with no gold files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
