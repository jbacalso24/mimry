# Releasing MIMRY

MIMRY targets CPython 3.11–3.13 on Windows, Linux, and macOS. That matrix is committed
in [`.github/workflows/ci.yml`](.github/workflows/ci.yml) and runs on every push and pull
request: 3 operating systems x 3 Python versions, plus two aggregate jobs. All dependencies
resolve from a standard index; MIMRY no longer pins any dependency to a Git commit.

A green CI run is **required** for a release, and it is **not a substitute for review**. CI
proves the checks that are automated; it says nothing about whether the change is the right
one, and the determinism golden values are only as good as the last time a human looked at
what they encode.

### Required checks

| Job | Runs on | What it proves |
| --- | --- | --- |
| `release-floor` | 3 OS x Python 3.11/3.12/3.13 | ruff format/check, `pytest -q`, in-memory graph identity, the real end-to-end determinism matrix, unlocked extracted-sdist parser + determinism suite, clean-wheel install and entrypoint smoke |
| `determinism-aggregate` | ubuntu-latest, needs `release-floor` | Every one of the 9 matrix cells emitted evidence, and all of them agree with each other and with the committed golden values on the canonical digest, the retrieval rankings, and the context pack |
| `benchmark-gate` | ubuntu-latest, locked env | The committed PASS matches fixture, cases, retrieval source, and exact thresholds before a fresh PASS is written only to `/tmp/artifact` and uploaded |

### Canonical versus operational evidence

Release evidence must distinguish the two. Canonical state is reproducible and is what the
golden values pin: relative file identities, symbols and line pointers, imports/exports,
graph nodes/edges/communities, semantic chunks, retrieval ranking order, and context-pack
evidence ordering. The operational envelope legitimately differs per run and must never
appear in a comparison: timestamps, generation UUIDs, absolute cache and root paths, mtimes
used only for cache invalidation, feedback event IDs, and raw SQLite byte layout. See
[`docs/determinism.md`](docs/determinism.md).

## Distribution channel

MIMRY 0.1.x is a **public alpha** distributed from the public source repository and manually reviewed GitHub release artifacts. Publishing to a Python package index is a separate decision that has not been made; do not claim `pip install mimry` from an index is supported until it is actually published there.

Install from a source checkout or a wheel downloaded from the matching GitHub release. All dependencies resolve from a standard index:

```bash
# Public source checkout
git clone <mimry-repo-url>
cd mimry
uv sync --locked

# Wheel downloaded from the matching GitHub release
uv venv /tmp/mimry-venv
uv pip install --python /tmp/mimry-venv/bin/python ./mimry-0.1.0-py3-none-any.whl
```

## Release checks

From a clean checkout:

```bash
uv sync --locked
uv run ruff format --check .
uv run ruff check .
uv run pytest -q
rm -rf dist
SOURCE_DATE_EPOCH="$(git show -s --format=%ct HEAD)" uv build
```

Then install the wheel into a clean environment and verify its dependency provenance:

```bash
uv venv /tmp/mimry-wheel-venv
uv pip install --python /tmp/mimry-wheel-venv/bin/python dist/*.whl
/tmp/mimry-wheel-venv/bin/mimry --help
/tmp/mimry-wheel-venv/bin/mimry status
```

The packaging tests verify the wheel's `Requires-Dist` entries and the sdist allow-list,
including both determinism matrix and aggregate scripts.

CI additionally extracts the sdist and, with an empty uv cache and no lockfile use, installs
its dependencies and runs the full parser and determinism surface against that unlocked
resolution: Python, TS/TSX, Java, PHP, Go, SQL and Markdown extraction, symbols and line
pointers, imports, calls, references, and the canonical digest. The sdist ships no `uv.lock`,
so this lane is the first place a newer parser release would appear. It invokes the matrix
script and source from the extracted artifact, never from the checkout. Import success is not
evidence of parser compatibility, which is why `tree-sitter` carries an upper bound
(`>=0.25.2,<0.26`) alongside the exact `tree-sitter-language-pack` pin.

## Release policy

1. Update `version` in `pyproject.toml` and the matching changelog section.
2. Re-run the unlocked-artifact parser and determinism lane before changing `tree-sitter` or `tree-sitter-language-pack`; keep both constrained in project metadata and regenerate `uv.lock`. Widening the `tree-sitter` upper bound requires that lane to pass and the golden determinism values to be re-confirmed, not regenerated to match.
3. Run the complete release checks above on a clean checkout.
4. Build twice with the same `SOURCE_DATE_EPOCH`, compare SHA-256 checksums, and review wheel metadata and sdist contents before tagging.
5. Create the GitHub release and upload artifacts only after the release record contains the exact commands, platform/Python versions, checksums, and results for every claimed platform. Publishing to an index or adding automated publishing requires a separate explicit release decision.
6. For the first public publication, follow [`docs/public-release.md`](docs/public-release.md): publish a clean reviewed snapshot rather than exposing the private development repository's historical objects.
