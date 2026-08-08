# Releasing MIMRY

MIMRY's supported release floor is CPython 3.11–3.13 on current GitHub-hosted Ubuntu and macOS runners. Windows remains best-effort until it is added to the required matrix. All dependencies resolve from a standard index; MIMRY no longer pins any dependency to a Git commit.

## Distribution channel

This release is an **internal direct-install artifact**. Distribution to a package index is a separate decision that has not been made; do not claim `pip install mimry` from an index is supported until it has.

Note that the technical blocker is gone: metadata no longer contains a direct Git reference, which is what PyPI-style indexes reject. Removing the Graphify pin removed that constraint.

Distribute through an approved internal Git release or artifact store and install either from an authorized checkout or a direct wheel path/URL with `uv`. All dependencies resolve from a standard index:

```bash
# Authorized checkout
git clone <internal-mimry-repo-url>
cd mimry
uv sync --locked

# Direct wheel file downloaded from the approved internal artifact store
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

The packaging tests verify the wheel's `Requires-Dist` entries and the sdist allow-list.

CI additionally extracts the sdist and, with an empty uv cache and no lockfile use, installs its dependencies and runs the TSX `LoginScreen` extraction test. This guards the functional behavior of unlocked artifact resolution, not only CLI startup.

## Release policy

1. Update `version` in `pyproject.toml` and the matching changelog section.
2. Re-run the unlocked artifact TSX compatibility test before changing `tree-sitter-language-pack`; keep the tested version constrained in project metadata and regenerate `uv.lock`.
3. Run the complete release checks above on a clean checkout.
4. Build twice with the same `SOURCE_DATE_EPOCH`, compare SHA-256 checksums, and review wheel metadata and sdist contents before tagging.
5. Create the internal Git release/upload only after the six CI matrix jobs pass. CI has no publishing credentials or publish step. Publishing to an index requires an explicit release decision.
