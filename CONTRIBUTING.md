# Contributing to MIMRY

MIMRY is an early public alpha. Focused bug fixes, portability improvements, privacy hardening, documentation corrections, and evidence-backed retrieval improvements are welcome.

## Before opening work

- Search existing issues and pull requests.
- Keep the change narrow; avoid mixing refactors with behavior changes.
- Open an issue first for large features, schema changes, new network behavior, telemetry, remote embeddings, or changes to privacy/security defaults.
- Report vulnerabilities through [`SECURITY.md`](SECURITY.md), not a public issue.
- Follow the [code of conduct](CODE_OF_CONDUCT.md).

## Development setup

Requirements:

- Python 3.11–3.13
- [`uv`](https://docs.astral.sh/uv/)
- Git

```bash
git clone <your-fork-url>
cd mimry
uv sync --locked
uv run pre-commit install
```

Run the local release floor before submitting:

```bash
uv run ruff format --check .
uv run ruff check .
uv run pytest -q
uv run python scripts/cross_platform_check.py
uv run python scripts/determinism_matrix.py
uv export --frozen --no-dev --no-emit-project \
  --format requirements-txt --output-file /tmp/mimry-runtime.txt
uvx pip-audit -r /tmp/mimry-runtime.txt --progress-spinner off
```

For packaging changes, also run:

```bash
rm -rf dist
SOURCE_DATE_EPOCH="$(git show -s --format=%ct HEAD)" uv build
```

Install the built wheel into a clean environment and smoke-test `mimry --help`. See [`RELEASING.md`](RELEASING.md) for the complete artifact gate.

## Change requirements

- Add or update tests for behavior changes.
- Preserve deterministic ordering, IDs, digests, and context output unless the change intentionally updates the documented contract.
- Never regenerate golden values merely to make a failing test pass; explain and review the semantic change.
- Keep indexing local-first and fail closed around secrets, unsafe roots, stale state, symlinks, and ambiguous relationships.
- Do not add telemetry, implicit network calls, provider credentials, or remote model requirements to the default path.
- Use generic paths and identities in docs and fixtures. Do not commit real usernames, home paths, tokens, keys, or private repository names.
- Keep `.mimry/`, `mimry-out/`, caches, build output, and local environments untracked.

## Pull requests

A pull request should include:

- the problem and intended behavior;
- files and contracts changed;
- exact verification commands and results;
- platform-specific evidence when applicable;
- security/privacy impact;
- documentation or changelog updates for user-facing changes.

The committed CI matrix covers Linux, macOS, and Windows on Python 3.11–3.13. A green matrix is required before release, but it does not replace review.

By contributing, you agree that your contribution is licensed under the repository's MIT License.
