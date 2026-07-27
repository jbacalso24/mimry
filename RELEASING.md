# Releasing MIMRY

MIMRY's supported release floor is CPython 3.11–3.13 on current GitHub-hosted Ubuntu and macOS runners. Windows remains best-effort until it is added to the required matrix. Git and network access are required when installing because Graphify is resolved from its pinned upstream commit.

## Release checks

From a clean checkout with no initialized Graphify submodule:

```bash
uv sync --locked
uv run ruff format --check .
uv run ruff check .
uv run pytest -q
rm -rf dist
uv build
```

Then install the wheel into a clean environment and verify its dependency provenance:

```bash
uv venv /tmp/mimry-wheel-venv
uv pip install --python /tmp/mimry-wheel-venv/bin/python dist/*.whl
/tmp/mimry-wheel-venv/bin/mimry --help
/tmp/mimry-wheel-venv/bin/mimry graphify status
```

The final status line must be `Runtime matches dependency policy: true`. The packaging tests also verify the wheel's `Requires-Dist` pin and the sdist allow-list.

## Release policy

1. Update `version` in `pyproject.toml` and the matching changelog section.
2. If Graphify changes, update the commit in the project dependency, wrapper policy constant, `THIRD_PARTY.md`, and artifact tests together; regenerate `uv.lock`.
3. Run the complete release checks above on a clean checkout.
4. Review wheel metadata and sdist contents before tagging.
5. Tag or publish only after the six CI matrix jobs pass. Publishing is intentionally manual and is not performed by CI.
