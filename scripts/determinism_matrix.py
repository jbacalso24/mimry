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
import hashlib
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

from mimry.digest import canonical_digest, canonical_state, normalize_context  # noqa: E402

# Canonical fields every permutation and every platform must agree on. The
# structural digest alone is not enough: it can match while the ranking an
# agent actually reads, or the context pack it is handed, has moved.
COMPARED_FIELDS = ("digest", "rankingsDigest", "contextDigest")


def _digest_of(value: object) -> str:
    blob = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


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
    # Mask the four operational values by their actual value -- not by shape.
    # Pattern-masking every timestamp-looking or hex-looking token also erased
    # canonical content, and dropping whole lines containing "Generated" threw
    # away MIMRY's own risk guidance. See mimry.digest.normalize_context.
    context_lines = normalize_context(
        context,
        root=root_path,
        index_path=idx,
        generation_id=ptr.get("generationId"),
        indexed_at=ptr.get("lastIndexedAt"),
    ).splitlines()

    payload = {
        "digest": canonical_digest(idx, ptr["rootId"]),
        "state": canonical_state(idx, ptr["rootId"]),
        "rankings": rankings,
        "contextLines": context_lines,
    }
    # Per-field digests travel to the aggregate CI job so a divergence names the
    # field that moved instead of only reporting "the hash differs".
    payload["rankingsDigest"] = _digest_of(rankings)
    payload["contextDigest"] = _digest_of(context_lines)
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


def _first_difference(expected: object, actual: object, path: str = "") -> str | None:
    """Locate the first differing leaf so a failure names a field, not a hash."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual)):
            if key not in expected:
                return f"{path}.{key}: missing on the left, present on the right"
            if key not in actual:
                return f"{path}.{key}: present on the left, missing on the right"
            found = _first_difference(expected[key], actual[key], f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(expected, list) and isinstance(actual, list):
        for index in range(max(len(expected), len(actual))):
            if index >= len(expected):
                return f"{path}[{index}]: extra entry {actual[index]!r}"
            if index >= len(actual):
                return f"{path}[{index}]: missing entry {expected[index]!r}"
            found = _first_difference(expected[index], actual[index], f"{path}[{index}]")
            if found:
                return found
        return None
    if expected != actual:
        return f"{path or '<root>'}:\n      expected {expected!r}\n      actual   {actual!r}"
    return None


def _print_field_diff(baseline: dict, results: dict) -> None:
    for label, payload in sorted(results.items()):
        if label == "baseline":
            continue
        for key in ("state", "rankings", "contextLines"):
            found = _first_difference(baseline[key], payload[key], key)
            if found:
                print(f"\n  first difference in {label} -> {found}", file=sys.stderr)
                break


def _print_golden_diff(golden: dict, baseline: dict) -> None:
    for key, golden_key in (("rankings", "rankings"), ("contextLines", "contextLines")):
        if golden_key not in golden:
            continue
        found = _first_difference(golden[golden_key], baseline[key], key)
        if found:
            print(f"\n  first difference vs golden -> {found}", file=sys.stderr)


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

        baseline = results["baseline"]
        diverged = False
        for field in COMPARED_FIELDS:
            values = {label: payload[field] for label, payload in results.items()}
            if len(set(values.values())) == 1:
                continue
            diverged = True
            print(f"\nDIVERGENCE -- {field} is not identical across permutations:", file=sys.stderr)
            for label, value in sorted(values.items()):
                print(f"  {label:22s} {value}", file=sys.stderr)
        if diverged:
            _print_field_diff(baseline, results)
            return 1

        canonical = {field: baseline[field] for field in COMPARED_FIELDS}
        print(f"\nCANONICAL DIGEST  {canonical['digest']}")
        print(f"rankings digest   {canonical['rankingsDigest']}")
        print(f"context digest    {canonical['contextDigest']}")
        print(f"permutations      {len(PERMUTATIONS)} identical")
        print(f"platform          {sys.platform} / python {sys.version.split()[0]}")

        if args.emit:
            # Carry the full evidence, not just the hashes: the aggregate job
            # needs the raw values to print a field-level diff when a platform
            # disagrees.
            Path(args.emit).write_text(
                json.dumps(
                    {
                        "platform": sys.platform,
                        "python": sys.version.split()[0],
                        **canonical,
                        "rankings": baseline["rankings"],
                        "contextLines": baseline["contextLines"],
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

        if args.update_golden:
            GOLDEN.write_text(
                json.dumps(
                    {
                        "canonicalDigest": canonical["digest"],
                        "rankingsDigest": canonical["rankingsDigest"],
                        "contextDigest": canonical["contextDigest"],
                        "rankings": baseline["rankings"],
                        "contextLines": baseline["contextLines"],
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            print(f"golden updated    {GOLDEN}")
            return 0

        if not GOLDEN.is_file():
            print(f"\nNo golden values at {GOLDEN}; run with --update-golden.", file=sys.stderr)
            return 1
        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        mismatches = [
            (name, golden.get(key), canonical[field])
            for name, key, field in (
                ("canonical digest", "canonicalDigest", "digest"),
                ("rankings digest", "rankingsDigest", "rankingsDigest"),
                ("context digest", "contextDigest", "contextDigest"),
            )
            if golden.get(key) != canonical[field]
        ]
        if mismatches:
            print("\nGOLDEN MISMATCH", file=sys.stderr)
            for name, expected, actual in mismatches:
                print(f"  {name}\n    expected {expected}\n    actual   {actual}", file=sys.stderr)
            _print_golden_diff(golden, baseline)
            return 1
        print("golden            match (digest, rankings, context)")
        return 0
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
