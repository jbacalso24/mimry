# Context Packs

`mimry context "<query>"` and `mimry preflight "<task>"` write an evidence-grade agent handoff to `mimry-out/context/latest.md`.

The pack is intentionally more than a ranked file list. It is designed to tell an agent what to read, why it matters, how files connect, what is risky, what to verify, and what not to touch unless needed.

Required sections:

- `# MIMRY Context Pack`
- query
- status summary: root, index freshness, Graphify freshness, and refresh action
- relevant files with scores, reasons, adapter evidence, and roles
- relevant symbols/entities when indexed
- Graphify relationships/communities/report signals when current
- explicit degradation when Graphify data is missing/stale; MIMRY must not invent links
- suggested reading order with rationale
- likely edit surfaces and likely non-edit supporting files
- risk notes for generated/cache paths, secrets/privacy-sensitive paths, broad dirty work, tests/docs/config support files
- suggested verification commands detected from config manifests and repo docs
- source-of-truth reminder
- final report checklist for agents, including a reminder to record `mimry feedback` after verification

Rules:

- Prefer relative paths in evidence sections.
- Do not dump full file contents.
- Do not print raw secrets or secret-looking values.
- Treat MIMRY as navigation. Source files, tests, build output, and operator verification remain truth.
