## Problem

<!-- What is wrong or missing, and the intended behavior. Link the issue if there is one. -->

## Change

<!-- Files and contracts changed. Call out anything that changes determinism, privacy or security defaults. -->

## Verification

<!-- The exact commands you ran and their results, with the platform. -->

## Checklist

- [ ] Tests added or updated for behavior changes
- [ ] `uv run ruff format --check .`, `uv run ruff check .` and `uv run pytest -q` pass
- [ ] `benchmarks/result.core.json` regenerated if anything under `src/mimry/` changed (see CONTRIBUTING.md)
- [ ] Documentation updated for user-facing changes
- [ ] No secrets, real usernames, home paths or private repository names in code, fixtures or docs
