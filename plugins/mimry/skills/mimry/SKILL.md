---
name: mimry
description: Use local repo memory at the start of every repo task instead of exploring by grep. Run preflight, read context packs, query indexed files/symbols/relationships, explain rankings, and record feedback after verified work. For an exact string already known, rg or git grep is fine.
---

# MIMRY

MIMRY is local repo intelligence for coding agents. It exists to stop blind grep/read loops and give agents a repo map before edits. It is not proof: source files, tests, build output, and user-visible behavior remain final truth.

Platform note: Bundled skill (Claude Code plugin or npx skills): prefer the MIMRY MCP tools when they are loaded. If the `mimry` command is not installed, run CLI commands as `uvx --from mimry==0.8.0 mimry <command>`, or install it with `uv tool install mimry`.

## Non-negotiable workflow

Use this skill for repo/project work: architecture discovery, debugging, implementation, refactors, audits, reviews, migrations, handoffs, and "where is X?" questions.

Once MIMRY has shown where to look, use `rg` (or the harness's Grep tool, or `git grep` when `rg` is missing) for exact strings you already know. Never use plain `grep -r`; it descends into `node_modules`, `.git` and build output.

1. Start with status/preflight, not broad search:

```bash
mimry status
mimry route "<user task>"
mimry preflight "<user task>"
```

2. Use the route result to pick the likely role, risk level, and next command. If delegating or handing off, write a focused brief:

```bash
mimry brief "<user task>" --agent <role>
```

3. Read the context pack that preflight names (its path is printed and returned as `context_path`), or the role brief, before editing:

```text
<context pack path>
.mimry/mimry-out/context/brief-<agent>.md
```

4. Open the suggested source/test files directly and verify against real code.
   Where a file lists `Read first: lines X-Y`, read those lines (for example with an offset and a limit) before the rest of the file.

5. After meaningful verified work, record feedback so future rankings improve:

```bash
mimry feedback --query "<task>" --context <context pack path> --opened "<files opened>" --changed "<files changed>" --verification "<command/result>" --outcome passed
```

## Stale/missing state rules

- If MIMRY is not initialized for an owned repo, run `mimry preflight "<task>"`; it initializes and builds useful local context.
- `preflight`, `context`, `brief`, `route`, `find`, `symbol`, `related`, `semantic`, `explain`, `path`, `why`, `callers` and `callees` reindex first when files changed since the last index, so they never answer from a stale one.
- `status` and `doctor` report a stale index; the next query updates it, and `mimry reindex` updates it ahead of time.
- Use `mimry refresh` or `mimry preflight --force-refresh "<task>"` when graph freshness matters more than speed.
- If graph artifacts are stale/missing, say so. Do not invent relationship paths.

## Focused navigation commands

Use focused MIMRY queries before broad grep/repeated file reads:

```bash
mimry context "<task>"
mimry route "<task>"
mimry route "<task>" --json
mimry brief "<task>" --agent <role>
mimry find "<query>"
mimry find "<query>" --semantic
mimry related "<query>"
mimry symbol "<name>"
mimry callers "<name>"
mimry callees "<name>"
mimry impact "<name or path>"
mimry history "<what changed>"
mimry explain "<task>"
mimry why <file-or-symbol> --query "<task>"
mimry path "<source>" "<target>"
mimry semantic "<query>"
mimry adapters
```

Prefer precise queries: `auth session refresh expiry` beats `bug`; `Next.js app router loading state` beats `frontend`.

## MCP-first when available

If MCP tools are loaded, prefer them over shell for lookup/context/workflow:

- `mimry_status`
- `mimry_preflight`
- `mimry_find`
- `mimry_related`
- `mimry_symbol`
- `mimry_path`
- `mimry_impact`
- `mimry_why`
- `mimry_feedback`

These are CLI commands, not MCP tools by default: `mimry init`, `mimry refresh`, `mimry route "<task>"`, `mimry brief "<task>" --agent <role>`, `mimry context "<task>"`, `mimry semantic "<query>"`, `mimry explain "<task>"`, `mimry adapters`.

The MCP server lists 9 core tools by default: `mimry_status`, `mimry_preflight`, `mimry_find`, `mimry_related`, `mimry_symbol`, `mimry_path`, `mimry_impact`, `mimry_why` and `mimry_feedback`.
Set `MIMRY_MCP_TOOLS=all` in the server's environment to list every MIMRY tool.
Commands with no tool, such as `mimry callers`, `mimry callees`, `mimry bench`, `mimry doctor`, `mimry report` and `mimry install`, stay CLI-only.

CLI fallback is fine when MCP is unavailable.

To find the commits whose message or changed files match a topic, use `mimry history "<what changed>"`; use `git log -S "<string>"` for an exact string in diffs.

## How to read MIMRY results

Each file in a context pack has a `Why:` line. Strongest evidence first:

- name matches / symbol matches / path matches: a task word is in the file's name, in a symbol it defines, or in its path
- content matches: a task word appears in the file's text
- connected to other matches: the graph links it to other results
- project config, helped in past tasks, similar wording: supporting evidence

`matched:` lists the task words that hit and `symbols:` the matching symbols.

Weak/limited signals include generic content hints, semantic-only matches, stale graph artifacts, and broad docs/plans for code-edit queries. Use `mimry explain`, `mimry why`, or `mimry path` when the ranking is surprising.

## Adapter expectations

Use `mimry adapters` to know what MIMRY can currently understand structurally. Current strengths are code/docs/project metadata, not arbitrary binary media. If an adapter is missing, use MIMRY for path/context narrowing, then inspect source manually.

Known limitation: image pixels are not understood by default yet. Image/media support should be treated as future adapter work unless this install reports an active image adapter.

## Load deeper references when needed

- `references/workflow.md` - task loop and fast path
- `references/commands.md` - CLI command guide
- `references/mcp.md` - MCP tool usage
- `references/feedback.md` - feedback recording
- `references/safety.md` - root/privacy safety

Load references when the task involves MCP, feedback, safety/root choice, stale indexes, or command details.

## Safety and source-truth rules

- Do not point MIMRY at `/`, `C:/`, `C:\\`, a whole home directory, dependency caches, or system folders.
- Do not paste raw secrets into feedback. MIMRY redacts likely secret values, but prevention is better.
- Generated paths such as `.mimry/`, `.mimry/mimry-out/`, and `.git/` are support artifacts, not source fixes.
- Never report success from MIMRY output alone. Verify with real source reads and the nearest tests/build/user-visible checks.
