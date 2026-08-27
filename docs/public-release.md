# First public publication

The existing private development repository must **not** be made public in place. Historical commits contain machine-local paths and development metadata that are not part of the product. The first public repository must start from one reviewed source snapshot so only the approved tree is published.

This is a publication procedure, not permission to publish. Changing repository visibility, renaming the private remote, creating the public repository, pushing the snapshot, and creating a release remain explicit owner actions.

## 1. Freeze an exact candidate

From a clean reviewed branch:

```bash
git status --short --branch
git rev-parse HEAD
git diff --check main...HEAD
```

Record the exact candidate SHA. Any later mutation invalidates the review and requires fresh verification.

## 2. Verify the candidate tree

Run the complete release checks from [`RELEASING.md`](../RELEASING.md), including the full tests, deterministic matrix, reproducible builds, artifact inspection, clean-wheel installation, and installed CLI smoke tests.

Run a current-tree secret scan and review every finding. Intentional fake-secret/privacy fixtures are not real credentials, but they still require human classification; never blanket-allow an entire source tree merely to make a scanner green.

```bash
gitleaks dir --redact --no-banner .
```

Also search tracked text for real names, machine-local home paths, work email domains, private repository names, and generated MIMRY/cache output. Use placeholders such as `/home/you`, `C:\\Users\\you`, `<mimry-repo-url>`, and `example.com` in public documentation and fixtures.

## 3. Export only the reviewed snapshot

Export the exact candidate into a destination that does not yet exist:

```bash
uv run python scripts/export_public_snapshot.py \
  --ref <candidate-sha> \
  --destination /var/tmp/mimry-public-snapshot
```

The exporter uses `git archive`, rejects unsafe archive members and generated/local paths, scans public text for non-placeholder home paths and non-example email addresses, and writes no Git history. It stages beside the destination and publishes with an OS-native atomic no-replace operation; an existing path, a dangling symlink, or a destination created during export makes publication fail without clobbering that path.

Re-run verification from the exported directory. At minimum:

```bash
cd /var/tmp/mimry-public-snapshot
uv sync --locked
uv run ruff format --check .
uv run ruff check .
uv run pytest -q
gitleaks dir --redact --no-banner .
```

Build artifacts again from the exported snapshot and compare their contents and checksums with the reviewed candidate.

## 4. Publish to a new empty public repository

Do not push the private repository's refs, tags, pull-request refs, backup branches, or object database. Create a new empty public repository and initialize the exported snapshot as its first commit using a public/noreply author identity.

Before the first push, confirm:

- the new repository has no imported history;
- `git rev-list --all --count` returns `1` after the root commit;
- `git remote -v` points only to the intended public repository;
- the source snapshot SHA and artifact checksums are recorded in the release evidence;
- secret scanning and the complete release floor passed on the exported tree.

## 5. Configure the public repository

Before announcing it:

- enable GitHub Actions and require the committed CI workflow;
- enable private vulnerability reporting;
- enable secret scanning and push protection when available;
- enable issues;
- protect the default branch from force pushes and deletion;
- require review and the release-floor, determinism-aggregate, dependency-audit, and benchmark checks before merges;
- set the description, topics, MIT license, and public-alpha status accurately;
- verify `README.md`, `SECURITY.md`, `CONTRIBUTING.md`, `LICENSE`, and release artifacts from the public URL.

A public URL and a green run on the exact public root commit are the final publication evidence. Local success alone is not proof that publication finished cleanly.
