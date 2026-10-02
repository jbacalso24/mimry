# Security policy

## Supported versions

MIMRY is currently a public alpha. Security fixes are applied to the latest commit on the default branch and to the latest tagged release when one exists. Older development snapshots are not supported.

## Reporting a vulnerability

Do **not** open a public issue for a suspected vulnerability, secret exposure, path traversal, unsafe cache operation, or privacy bypass.

Use GitHub's private vulnerability reporting for this repository:

1. Open the repository's **Security** tab.
2. Choose **Report a vulnerability**.
3. Include the affected version or commit, operating system, reproduction steps, impact, and any suggested mitigation.

If private vulnerability reporting is unavailable, contact the repository owner through the private contact method shown on their GitHub profile. Do not include live credentials, private keys, personal source files, or unredacted cache contents in the first message.

You should receive an acknowledgement within seven days. Disclosure timing will be coordinated after the issue is reproduced and a fix is available.

## Security boundary

MIMRY is local-first, but local software still processes untrusted filenames and source trees. Its generated indexes and context packs are navigation aids, not a security boundary or source of truth.

- Do not point MIMRY at a home directory, drive root, credential dump, or unknown archive.
- Review the selected root and generated output before sharing either one.
- Keep `.mimry/`, `mimry-out/`, local caches, and feedback data out of version control.
- Treat scanner exclusions and redaction as defense in depth, not permission to store secrets in repositories.
- MIMRY does not require cloud credentials, remote embeddings, or telemetry for its default workflow.
