# Changelog

Notable user-facing changes are recorded here. MIMRY is pre-1.0; compatibility changes may still occur, but the supported release floor is tested before a release is tagged.

## Unreleased

### Packaging and support floor

- Pin Graphify to one Git commit in published package metadata, so checkout installs and wheel installs resolve the same code.
- Report Graphify runtime source, version, commit, URL, and policy match instead of presenting the configured pin as observed provenance.
- Restrict source distributions to reviewed source, tests, documentation, and license files; exclude vendored, generated, cache, build, and local-work bulk.
- Add artifact contract tests and Linux/macOS CI for Python 3.11, 3.12, and 3.13.
