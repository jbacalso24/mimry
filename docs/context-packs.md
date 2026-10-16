# Context Packs

`mimry context "<query>"` and `mimry preflight "<task>"` write an evidence-grade agent handoff to `.mimry/mimry-out/context/<task>-<hash>.md`, and the path is printed and returned as `context_path`.
Each task gets its own file named from the task, so parallel agents in one checkout do not overwrite each other.
The newest 20 are kept, and `latest.md` is a copy of the most recent one.

The pack is intentionally more than a ranked file list. It is designed to tell an agent what to read, why it matters, how files connect, what is risky, what to verify, and what not to touch unless needed.

Required sections:

- `# MIMRY Context Pack`
- query
- status: root, index freshness, graph freshness, and refresh action (condensed)
- relevant files with scores, reasons, adapter evidence, and roles (compact), and for each file the line ranges to read first (`Read first: lines 120-168 (`name`)`), when its functions, classes or sections match the query
- relevant symbols/entities when indexed
- graph relationships/communities/report signals when current
- explicit degradation when graph data is missing/stale; MIMRY must not invent links
- reading order with roles (no duplicate reasons)
- edit surfaces and support files (path lists only)
- risks: generated/cache/dirty paths and degradation warnings (condensed)
- verify with: suggested verification commands detected from package.json, pyproject.toml, Makefile and justfile targets, tox and nox sessions, and CI workflow run steps
  A nested package's command runs from its own directory (`cd adapters/bun && bun run test`) with its own package manager.
  Tool-name guesses (`uv run pytest` from pyproject.toml) appear only when the project names no check command.
  Repo docs such as README.md, AGENTS.md and CLAUDE.md are never a source of commands.

Rules:

- Prefer relative paths in evidence sections.
- Do not dump full file contents.
- Do not print raw secrets or secret-looking values.
- Treat MIMRY as navigation. Source files, tests, build output, and operator verification remain truth.
