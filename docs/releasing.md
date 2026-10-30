# Releasing MIMRY

This page lists the steps of a release in order.
The full policy, the version rules and the distribution channels are in [`RELEASING.md`](../RELEASING.md).

## Steps

1. Bump `version` in `pyproject.toml`, then run `uv lock` so `uv.lock` matches it.
2. Add the release's section to `CHANGELOG.md`, as `RELEASING.md` describes.
3. Open the release pull request against `main`.
   It changes `pyproject.toml`, so the release gate runs on it.
4. Wait for both checks to be green on the release pull request: the release gate on Ubuntu and Windows, and CI.
5. Merge the release pull request.
6. Tag the merged commit on `main` as `vX.Y.Z` and push the tag.
7. The tag runs `release.yml`, which publishes to PyPI and creates the GitHub Release.
8. Review the release notes on the GitHub Release and edit them if they need work.

## The release gate

The gate is [`scripts/release_gate.py`](../scripts/release_gate.py), run by [`.github/workflows/release-gate.yml`](../.github/workflows/release-gate.yml).
It builds the wheel, then checks the install into a clean home, indexing time against the last release on PyPI, recovery from a deleted index generation, an upgrade from the last release, and the redaction corpus at 64 KB.

Run it locally from the repository root, with network access to PyPI:

```bash
uv build
uv run python scripts/release_gate.py --wheel dist/mimry-<version>-py3-none-any.whl
```

Each check prints `PASS` or `FAIL`, and the script exits 1 when any check fails.
Do not tag a release until the gate is green on the release pull request.
The workflow runs when the release pull request changes `pyproject.toml`; the tag itself is not gated by a workflow, so this rule is the maintainer's to keep.
