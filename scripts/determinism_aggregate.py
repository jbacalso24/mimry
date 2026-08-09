#!/usr/bin/env python3
"""Compare determinism evidence collected from every CI matrix job.

Each OS/Python job runs ``scripts/determinism_matrix.py --emit`` and uploads
one JSON artifact. This job downloads them all and actually diffs them.

It fails when:

* an expected OS/Python job did not produce an artifact (a skipped or crashed
  platform must not read as agreement),
* any compared field differs between platforms,
* any compared field differs from the committed golden values.

Structural agreement is not sufficient. The canonical digest can match while
the retrieval ranking an agent reads, or the context pack it is handed, has
moved -- so rankings and context are compared as first-class fields and a
divergence prints the field that moved, not just a hash.

    python scripts/determinism_aggregate.py --artifacts artifacts/ \
        --expect-os ubuntu-latest,macos-latest,windows-latest \
        --expect-python 3.11,3.12,3.13
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GOLDEN = REPO / "tests" / "fixtures" / "determinism_golden.json"

sys.path.insert(0, str(REPO / "scripts"))

from determinism_matrix import COMPARED_FIELDS, _first_difference  # noqa: E402

GOLDEN_KEY = {
    "digest": "canonicalDigest",
    "rankingsDigest": "rankingsDigest",
    "contextDigest": "contextDigest",
}


def collect(artifacts: Path) -> dict[str, dict]:
    """Map `digest-<os>-<python>` to its payload."""
    found = {}
    for path in sorted(artifacts.rglob("digest-*.json")):
        found[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--expect-os", required=True, help="Comma-separated runner labels")
    parser.add_argument("--expect-python", required=True, help="Comma-separated Python versions")
    args = parser.parse_args()

    expected = {
        f"digest-{os_name}-{python}"
        for os_name in args.expect_os.split(",")
        for python in args.expect_python.split(",")
    }
    found = collect(args.artifacts)

    missing = sorted(expected - set(found))
    if missing:
        print("MISSING DETERMINISM ARTIFACTS -- these matrix jobs produced no evidence:", file=sys.stderr)
        for name in missing:
            print(f"  {name}", file=sys.stderr)
        print("\nA platform that did not report cannot be assumed to agree.", file=sys.stderr)
        return 1

    unexpected = sorted(set(found) - expected)
    for name in unexpected:
        print(f"note: extra artifact not in the expected matrix: {name}")

    golden = json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.is_file() else {}
    if not golden:
        print(f"No golden values at {GOLDEN}.", file=sys.stderr)
        return 1

    print(f"{'job':44s} {'digest':12s} {'rankings':12s} {'context':12s}")
    for name in sorted(found):
        payload = found[name]
        print(
            f"{name:44s} "
            f"{payload['digest'][:10]:12s} "
            f"{payload['rankingsDigest'][:10]:12s} "
            f"{payload['contextDigest'][:10]:12s}"
        )

    failed = False
    reference_name = sorted(found)[0]
    reference = found[reference_name]

    for field in COMPARED_FIELDS:
        values = {name: payload[field] for name, payload in found.items()}
        if len(set(values.values())) != 1:
            failed = True
            print(f"\nCROSS-PLATFORM DIVERGENCE in {field}:", file=sys.stderr)
            for name, value in sorted(values.items()):
                print(f"  {name:44s} {value}", file=sys.stderr)
            raw = {"rankingsDigest": "rankings", "contextDigest": "contextLines"}.get(field)
            if raw:
                for name, payload in sorted(found.items()):
                    if payload[field] == reference[field]:
                        continue
                    found_diff = _first_difference(reference[raw], payload[raw], raw)
                    if found_diff:
                        print(f"  first difference {reference_name} vs {name} -> {found_diff}", file=sys.stderr)
                        break

        expected_value = golden.get(GOLDEN_KEY[field])
        if expected_value is not None and reference[field] != expected_value:
            failed = True
            print(
                f"\nGOLDEN MISMATCH in {field}\n  expected {expected_value}\n  actual   {reference[field]}",
                file=sys.stderr,
            )
            raw = {"rankingsDigest": "rankings", "contextDigest": "contextLines"}.get(field)
            if raw and raw in golden:
                found_diff = _first_difference(golden[raw], reference[raw], raw)
                if found_diff:
                    print(f"  first difference vs golden -> {found_diff}", file=sys.stderr)

    if failed:
        return 1

    print(f"\nAll {len(found)} matrix jobs agree on every canonical field, and match the committed golden values.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
