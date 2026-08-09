#!/usr/bin/env python3
"""Prove the *real* MIMRY pipeline is reproducible.

The older cross-platform check hashed a graph built from fixed in-memory input.
That proved the graph engine was portable but exercised no scanner, no parser,
no SQLite, and no context pack -- exactly the layers where filesystem order,
absolute paths, and hash seeds leak in.

This drives the whole pipeline over a frozen fixture repository:

    filesystem scan -> Python/TS/Java/PHP/Go/SQL/Markdown parsing -> symbols and
    line pointers -> imports/exports/calls/references -> graph and communities
    -> SQLite/FTS -> local semantic index -> context pack

...once per permutation, each in its own clean process with its own cache, and
compares the canonical semantic digest plus a few ordered outputs.

Permutations cover file-creation order, absolute checkout root, PYTHONHASHSEED,
repeated clean-cache runs, and locale/timezone.

    python scripts/determinism_matrix.py
    python scripts/determinism_matrix.py --update-golden
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "determinism_repo"
GOLDEN = REPO / "tests" / "fixtures" / "determinism_golden.json"

sys.path.insert(0, str(REPO / "src"))


def fixture_files() -> list[str]:
    """Canonical relative paths of the frozen fixture, in canonical order."""
    return sorted(
        p.relative_to(FIXTURE).as_posix() for p in FIXTURE.rglob("*") if p.is_file() and p.name != ".gitattributes"
    )


def materialize(target: Path, *, reverse: bool) -> None:
    """Copy the fixture into ``target``, creating files in the requested order.

    Creation order is the whole point: on most filesystems it determines
    readdir order, which is what leaked into artifacts before.
    """
    names = fixture_files()
    if reverse:
        names = list(reversed(names))
    target.mkdir(parents=True, exist_ok=True)
    for name in names:
        dest = target / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Byte copy, not text: line endings must survive untouched.
        dest.write_bytes((FIXTURE / name).read_bytes())


# ---------------------------------------------------------------- worker side


def run_worker(root: str, out: str) -> int:
    """Index ``root`` from scratch and write its canonical outputs to ``out``."""
    from mimry.commands import cmd_context, cmd_index, cmd_init
    from mimry.digest import canonical_digest, canonical_state
    from mimry.search import find_rows
    from mimry.storage import load_pointer

    root_path = Path(root).resolve()
    args = argparse.Namespace(root=str(root_path), root_type="repo", skip_graph=False)
    cmd_init(args)
    cmd_index(args)

    ptr = load_pointer(root_path)
    idx = Path(ptr["indexPath"])

    # Retrieval ordering is canonical state too: a tied FTS cutoff or an
    # unordered semantic read shows up here and nowhere else.
    queries = ["session login refresh", "payment checkout", "user model", "sessions table"]
    rankings = {}
    for query in queries:
        rows = find_rows(idx, query, limit=8, graph=True, root=root_path, root_id=ptr["rootId"], semantic=True)
        rankings[query] = [{"path": r["path"], "reason": r["reason"]} for r in rows]

    cmd_context(argparse.Namespace(root=str(root_path), query="session refresh flow", semantic=True))
    context = (root_path / ".mimry" / "mimry-out" / "context" / "latest.md").read_text(encoding="utf-8")
    # The pack embeds the absolute root and a generation timestamp; both are
    # operational. Compare the evidence ordering, which is not.
    context_lines = [
        line for line in context.splitlines() if line.strip() and str(root_path) not in line and "Generated" not in line
    ]

    payload = {
        "digest": canonical_digest(idx, ptr["rootId"]),
        "state": canonical_state(idx, ptr["rootId"]),
        "rankings": rankings,
        "contextLines": context_lines,
    }
    Path(out).write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    print(payload["digest"])
    return 0


# ---------------------------------------------------------------- driver side

PERMUTATIONS = [
    # label,                 reverse, env overrides
    ("baseline", False, {"PYTHONHASHSEED": "0"}),
    ("reversed-creation", True, {"PYTHONHASHSEED": "0"}),
    ("hashseed-1", False, {"PYTHONHASHSEED": "1"}),
    ("hashseed-2", True, {"PYTHONHASHSEED": "2"}),
    ("hashseed-4177", False, {"PYTHONHASHSEED": "4177"}),
    ("hashseed-random", True, {"PYTHONHASHSEED": "random"}),
    ("locale-tz", False, {"PYTHONHASHSEED": "3", "LC_ALL": "C", "TZ": "Asia/Kolkata"}),
    ("repeat-clean-cache", False, {"PYTHONHASHSEED": "5"}),
]


def run_permutation(workspace: Path, label: str, index: int, reverse: bool, env_overrides: dict) -> dict:
    """One clean process, one clean cache, one fresh absolute checkout root."""
    # A distinct, differently-shaped absolute root per permutation is what
    # catches identities derived from the checkout path.
    root = workspace / f"root-{index:02d}-{label}" / "nested" / "checkout"
    materialize(root, reverse=reverse)

    env = dict(os.environ)
    env["MIMRY_CACHE_HOME"] = str(workspace / f"cache-{index:02d}")
    env["PYTHONPATH"] = str(REPO / "src")
    env.update(env_overrides)

    out = workspace / f"out-{index:02d}.json"
    result = subprocess.run(
        [sys.executable, __file__, "--worker", "--root", str(root), "--out", str(out)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO),
    )
    if result.returncode != 0:
        raise SystemExit(f"permutation {label} failed:\n{result.stdout}\n{result.stderr}")
    return json.loads(out.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--root", help=argparse.SUPPRESS)
    parser.add_argument("--out", help=argparse.SUPPRESS)
    parser.add_argument("--update-golden", action="store_true", help="Rewrite the committed golden digest")
    parser.add_argument("--emit", help="Also write this platform's digest to a JSON file for CI aggregation")
    args = parser.parse_args()

    if args.worker:
        return run_worker(args.root, args.out)

    workspace = Path(tempfile.mkdtemp(prefix="mimry-determinism-"))
    try:
        results = {}
        for index, (label, reverse, env_overrides) in enumerate(PERMUTATIONS):
            results[label] = run_permutation(workspace, label, index, reverse, env_overrides)
            print(f"{label:22s} {results[label]['digest']}")

        digests = {label: payload["digest"] for label, payload in results.items()}
        unique = sorted(set(digests.values()))
        if len(unique) != 1:
            print("\nDIVERGENCE -- canonical digests are not identical:", file=sys.stderr)
            for label, digest in sorted(digests.items()):
                print(f"  {label:22s} {digest}", file=sys.stderr)
            baseline = results["baseline"]
            for label, payload in sorted(results.items()):
                if payload["digest"] == baseline["digest"]:
                    continue
                for key in ("state", "rankings", "contextLines"):
                    if payload[key] != baseline[key]:
                        print(f"  first differing section for {label}: {key}", file=sys.stderr)
                        break
            return 1

        # Ordered outputs must match too: a digest can agree while the ranking
        # that an agent actually reads does not.
        baseline = results["baseline"]
        for label, payload in results.items():
            for key in ("rankings", "contextLines"):
                if payload[key] != baseline[key]:
                    print(f"\nDIVERGENCE -- {key} differs for {label}", file=sys.stderr)
                    return 1

        digest = unique[0]
        print(f"\nCANONICAL DIGEST  {digest}")
        print(f"permutations      {len(PERMUTATIONS)} identical")
        print(f"platform          {sys.platform} / python {sys.version.split()[0]}")

        if args.emit:
            Path(args.emit).write_text(
                json.dumps({"platform": sys.platform, "python": sys.version.split()[0], "digest": digest}, indent=2),
                encoding="utf-8",
            )

        if args.update_golden:
            GOLDEN.write_text(json.dumps({"canonicalDigest": digest}, indent=2) + "\n", encoding="utf-8")
            print(f"golden updated    {GOLDEN}")
            return 0

        if not GOLDEN.is_file():
            print(f"\nNo golden digest at {GOLDEN}; run with --update-golden.", file=sys.stderr)
            return 1
        expected = json.loads(GOLDEN.read_text(encoding="utf-8"))["canonicalDigest"]
        if digest != expected:
            print(f"\nGOLDEN MISMATCH\n  expected {expected}\n  actual   {digest}", file=sys.stderr)
            return 1
        print("golden            match")
        return 0
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
