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

## Opening a pull request

1. Fork [jbacalso24/mimry](https://github.com/jbacalso24/mimry) on GitHub, clone your fork, and add the main repository as `upstream`:

   ```bash
   git clone https://github.com/<you>/mimry.git
   cd mimry
   git remote add upstream https://github.com/jbacalso24/mimry.git
   ```

2. Start a branch from the latest `main`.
   Name it after the kind of change: `fix/`, `feat/`, `docs/`, `bench/` or `chore/`, then a few words.

   ```bash
   git fetch upstream
   git switch -c fix/short-description upstream/main
   ```

3. Make the change with its tests (see [Change requirements](#change-requirements)).
   Write each commit subject as what the change does for users, in the imperative: "Stop install hanging on Windows", not "Fixed bug".
   Put details in the commit body as short bullets.

4. Run the [release floor](#development-setup).
   At minimum, `ruff format --check`, `ruff check` and `pytest -q` must pass.

5. If you changed any file under `src/mimry/`, regenerate the benchmark evidence and commit it with your change:

   ```bash
   uv run python scripts/agent_usefulness_benchmark.py --repeat 3 --graph --out benchmarks/result.core.json
   uv run python scripts/agent_usefulness_benchmark.py --validate-report benchmarks/result.core.json
   ```

   The committed report records a hash of the source, so any source edit, even a comment, fails `tests/test_benchmark_quality_gate.py` and the `benchmark-gate` CI job until it is regenerated.
   It can be generated on any platform.
   Run `git diff benchmarks/result.core.json` before committing: unless your change is meant to affect retrieval, only `retrieval_source_sha256` and timings should change.
   If rankings or quality metrics moved, say so in the pull request and why.

6. If the determinism gate fails because your change intentionally changes canonical output, update the golden values with `uv run python scripts/determinism_matrix.py --update-golden` and explain the semantic change in the pull request.
   Never update them only to make a red gate pass (see [`docs/determinism.md`](docs/determinism.md)).

7. Leave the version number alone; maintainers bump it when releasing.

8. Push the branch to your fork and open a pull request against `main`:

   ```bash
   git push -u origin fix/short-description
   ```

   Title it like a commit subject, because pull requests are squash-merged and the title becomes the commit on `main`.
   Fill in the template, covering:
   - the problem and intended behavior;
   - files and contracts changed;
   - exact verification commands and results, with the platform;
   - security and privacy impact;
   - documentation updates for user-facing changes.

9. CI must be green before review is complete.
   It runs lint, the tests and the determinism matrix on Linux, macOS and Windows with Python 3.11–3.13, plus the benchmark gate.
   A green matrix does not replace review.
   To pick up newer `main` commits, rebase and update your branch:

   ```bash
   git fetch upstream
   git rebase upstream/main
   git push --force-with-lease
   ```

A maintainer reviews, squash-merges, and deletes the branch.

By contributing, you agree that your contribution is licensed under the repository's MIT License.
