# MIMRY MCP

When MCP tools are available, prefer them over shell commands for lookup, preflight, context generation, and feedback.

Primary workflow tools:

- `mimry_status(root?)`
- `mimry_preflight(query, root?, force_refresh?)`

Navigation and explanation tools:

- `mimry_find(query, root?, limit?, semantic?)`
- `mimry_related(query, root?, limit?)`
- `mimry_symbol(name, root?, relations?, depth?, file?)`
  relations "callers" or "callees" lists the calls around a symbol; each result's `confidence` is `EXTRACTED` or `INFERRED`; check INFERRED ones first
- `mimry_path(source, target, root?)`
- `mimry_impact(target?, file?, changed?, depth?, root?)`
- `mimry_why(surface, query, root?, limit?)`

Feedback/tooling tools:

- `mimry_feedback(query, root?, context?, suggested?, opened?, changed?, missed?, ignored?, verification?, outcome?, notes?)`

Other tools, such as `mimry_list_adapters`, `mimry_context` and `mimry_history`, are listed only with `MIMRY_MCP_TOOLS=all` in the server's environment.

CLI commands (not MCP tools by default):

- `mimry init`
- `mimry refresh`
- `mimry route "<task>"`
- `mimry brief "<task>" --agent <role>`
- `mimry context "<task>"`
- `mimry semantic "<query>"`
- `mimry explain "<task>"`
- `mimry adapters`
- `mimry history "<what changed>"`

Rules:

- Start a task with `mimry_preflight` instead of exploring by grep; for an exact string you already know, use `rg` or `git grep` directly.
- Read the context pack preflight names (`context_path` over MCP) before editing.
- Where a result lists `ranges` or `Read first: lines X-Y`, read those lines (for example with an offset and a limit) before the rest of the file.
- Use `mimry_why`, or `mimry explain "<task>"` on the CLI, when a ranking is surprising.
- Use `mimry_path` only as graph evidence; if no path is found, do not invent one.
- Use CLI fallback when MCP is unavailable or the agent host has not loaded the server.
