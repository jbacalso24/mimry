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

A separate [`audit.yml`](.github/workflows/audit.yml) workflow runs `pip-audit` against the exact locked runtime dependencies whenever `uv.lock` or `pyproject.toml` changes, and weekly.
It is not a required check, so a newly published advisory never blocks unrelated pull requests; a red run means the lock needs an upgrade.

### Canonical versus operational evidence

Release evidence must distinguish the two. Canonical state is reproducible and is what the
golden values pin: relative file identities, symbols and line pointers, imports/exports,
graph nodes/edges/communities, semantic chunks, retrieval ranking order, and context-pack
evidence ordering. The operational envelope legitimately differs per run and must never
appear in a comparison: timestamps, generation UUIDs, absolute cache and root paths, mtimes
used only for cache invalidation, feedback event IDs, and raw SQLite byte layout. See
[`docs/determinism.md`](docs/determinism.md).

## Distribution channel

MIMRY is published to [PyPI](https://pypi.org/project/mimry/) and attached to [GitHub Releases](https://github.com/jbacalso24/mimry/releases).
Pushing a `vX.Y.Z` tag runs [`.github/workflows/release.yml`](.github/workflows/release.yml), which checks that the tag matches the `pyproject.toml` version, builds the sdist and wheel with `SOURCE_DATE_EPOCH` set from the tagged commit, publishes to PyPI through Trusted Publishing (OIDC, no stored token), and creates the GitHub Release with the artifacts.
The PyPI Trusted Publisher is configured for repository `jbacalso24/mimry`, workflow `release.yml`, and environment `pypi`.

`main` accepts changes only through pull requests, so tag the merged commit on `main`, not a local branch commit.

### Arch Linux (AUR)

Arch users install the [`mimry`](https://aur.archlinux.org/packages/mimry) AUR package with `yay -S mimry`.
Arch ships tree-sitter and its grammar pack at versions other than the ones MIMRY pins, so the package carries its own environment in `/usr/lib/mimry`, built from the hash-pinned requirements in `uv.lock`.
[`packaging/aur/`](packaging/aur) holds the `PKGBUILD` template, `update.sh`, which renders it for a released version, and `smoke.sh`, which checks an installed package.

After PyPI publishing, the release workflow runs two more jobs.
`aur-build` renders the package in an Arch container for the sdist just published, builds and installs it, and runs the smoke test.
`aur-publish` then pushes `PKGBUILD`, `.SRCINFO` and the requirements file to the AUR over SSH, in a separate job so the key never shares a job with downloaded code.
It pins the AUR's Ed25519 host key, and a rerun for a version the AUR already has changes nothing.
If the AUR fails after PyPI succeeded, rerun only the failed jobs.

One-time setup, done by the maintainer:

1. Create an account on [aur.archlinux.org](https://aur.archlinux.org) and add a dedicated SSH public key to it, used for nothing else.
2. In the repository settings, create an environment named `aur`, limit it to `v*` tags, and add the private key as the environment secret `AUR_SSH_PRIVATE_KEY`.

Until the secret exists, `aur-publish` succeeds without publishing and leaves a notice, so a release still proves the package builds.
The first successful push creates the package on the AUR, owned by the account that holds the key.

To publish a packaging fix without a new MIMRY release, bump `pkgrel` with `update.sh <version> <out-dir> <pkgrel>` from a checkout of the release tag, on Arch as a normal user, then commit the three files to the AUR repository by hand.

```bash
uv tool install mimry          # users
git clone https://github.com/jbacalso24/mimry.git && cd mimry && uv sync --locked   # contributors
```

## Release checks

From a clean checkout:

```bash
uv sync --locked
uv run ruff format --check .
uv run ruff check .
uv run pytest -q
uv export --frozen --no-dev --no-emit-project \
  --format requirements-txt --output-file /tmp/mimry-runtime.txt
uvx pip-audit -r /tmp/mimry-runtime.txt --progress-spinner off
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
5. Merge the release pull request, then tag the merged commit and push the tag (`git tag -a vX.Y.Z <sha> -m "mimry X.Y.Z" && git push origin vX.Y.Z`); the release workflow publishes to PyPI and GitHub Releases.
