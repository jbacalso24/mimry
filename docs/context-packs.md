# Context Packs

`mimry context "<query>"` and `mimry preflight "<task>"` write an evidence-grade agent handoff to `.mimry/mimry-out/context/latest.md`.

The pack is intentionally more than a ranked file list. It is designed to tell an agent what to read, why it matters, how files connect, what is risky, what to verify, and what not to touch unless needed.

Required sections:

- `# MIMRY Context Pack`
- query
- status: root, index freshness, graph freshness, and refresh action (condensed)
- relevant files with scores, reasons, adapter evidence, and roles (compact)
- relevant symbols/entities when indexed
- graph relationships/communities/report signals when current
- explicit degradation when graph data is missing/stale; MIMRY must not invent links
- reading order with roles (no duplicate reasons)
- edit surfaces and support files (path lists only)
- risks: generated/cache/dirty paths and degradation warnings (condensed)
- verify with: suggested verification commands detected from config manifests and repo docs

Rules:

- Prefer relative paths in evidence sections.
- Do not dump full file contents.
- Do not print raw secrets or secret-looking values.
- Treat MIMRY as navigation. Source files, tests, build output, and operator verification remain truth.
