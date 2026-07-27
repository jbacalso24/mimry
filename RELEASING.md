# Releasing MIMRY

MIMRY's supported release floor is CPython 3.11–3.13 on current GitHub-hosted Ubuntu and macOS runners. Windows remains best-effort until it is added to the required matrix. Git and network access are required when installing because Graphify is resolved from its pinned upstream commit.

## Distribution channel

This release is an **internal direct-install artifact**, not a PyPI release. Its wheel and sdist metadata intentionally contain a Graphify Git URL pinned to an exact commit. PyPI and PyPI-style indexes reject such direct references, so do not upload these artifacts to them and do not claim that `pip install mimry` from an index is supported.

Distribute through an approved internal Git release or artifact store and install either from an authorized checkout or a direct wheel path/URL with `uv`. The installer must be able to reach GitHub to resolve Graphify:

```bash
# Authorized checkout
git clone <internal-mimry-repo-url>
cd mimry
uv sync --locked

# Direct wheel file downloaded from the approved internal artifact store
uv venv /tmp/mimry-venv
uv pip install --python /tmp/mimry-venv/bin/python ./mimry-0.1.0-py3-none-any.whl
```

Moving to a package index requires a separate release decision and proof of an equivalent index-publishable Graphify version. Do not remove the Git commit pin merely to satisfy an index.

## Release checks

From a clean checkout with no initialized Graphify submodule:

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
/tmp/mimry-wheel-venv/bin/mimry graphify status
```

The final status line must be `Runtime matches dependency policy: true`. The packaging tests also verify the wheel's `Requires-Dist` pin and the sdist allow-list.

CI additionally extracts the sdist and, with an empty uv cache and no lockfile use, installs its dependencies and runs the TSX `LoginScreen` extraction test. This guards the functional behavior of unlocked artifact resolution, not only CLI startup.

## Release policy

1. Update `version` in `pyproject.toml` and the matching changelog section.
2. If Graphify changes, initialize `vendor/graphify`, fetch and checkout the reviewed commit, and stage the resulting **gitlink** (`git add vendor/graphify`). Update the project dependency, wrapper policy constant, `THIRD_PARTY.md`, and artifact tests to the same commit; regenerate `uv.lock`. Verify the staged gitlink with `git rev-parse :vendor/graphify` and include it in review.
3. Re-run the unlocked artifact TSX compatibility test before changing `tree-sitter-language-pack`; keep the tested version constrained in project metadata and regenerate `uv.lock`.
4. Run the complete release checks above on a clean checkout.
5. Build twice with the same `SOURCE_DATE_EPOCH`, compare SHA-256 checksums, and review wheel metadata and sdist contents before tagging.
6. Create the internal Git release/upload only after the six CI matrix jobs pass. CI has no publishing credentials or publish step. Never publish this direct-reference release to PyPI or a PyPI-style index.
