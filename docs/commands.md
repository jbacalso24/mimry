# Commands in detail

This page lists every command, then covers filters, pagination, diagnostics and plan trees.

## All commands

Add `--verbose` (`-v`) to any command for paths, scores, and raw ranking reasons.

| Command | Use it to |
|---|---|
| `mimry preflight "<task>"` | Start a task: context pack plus the files to read first |
| `mimry find "<query>"` | Rank files in the current indexed directory and its descendants; `--everywhere` searches the whole repository and explicit `--path-glob` overrides the default scope. Repeat `--language`, `--path-glob`, or `--exclude-glob`, and page with `--offset`/`--limit` (`--semantic` blends fuzzy matches; `--snippets` adds bounded, live-source excerpts) |
| `mimry related "<query>"` | Find files connected through the graph |
| `mimry semantic "<query>"` | Fuzzy recall for "I remember something like..." |
| `mimry symbol <name>` | Find where a symbol or Markdown heading is defined, or a file by name |
| `mimry context "<task>"` | Write a context pack without the preflight checks |
| `mimry explain "<task>"` | Files, key symbols, and how they connect |
| `mimry path <a> <b>` | Shortest relationship path between two files or symbols |
| `mimry callers <name>` | Functions that call a function, from the graph (`--depth` follows the chain up to 3 calls, `--file` picks one of several, `--json` for tools) |
| `mimry callees <name>` | Functions that a function calls, with the same options |
| `mimry why <file> --query "<task>"` | Why a file ranks for a query |
| `mimry route "<task>"` | Recommend an agent role (`--json` for tools) |
| `mimry brief "<task>" --agent <role>` | Write a role-specific brief |
| `mimry init` / `mimry index` | Set up a repo and build the index |
| `mimry reindex` / `mimry refresh` | Update the index ahead of time (`--full` re-parses everything); query commands update a stale index on their own |
| `mimry status` | Is the index current? (`--verify` re-hashes every file) |
| `mimry doctor` | Read-only install, parser, index, and cache diagnostics (`--json` for tools) |
| `mimry watch` | Poll for changes and refresh when they settle |
| `mimry git-hooks` | Keep the index fresh with git hooks (`install`/`uninstall`/`status`); the post-commit hook also records feedback from the commit, and re-running `mimry git-hooks` upgrades existing hooks |
| `mimry feedback ...` | Record what helped; `feedback stats`, `list`, `show <id>` read it back; `feedback stats` also shows, for the last 30 days, how many preflights ran, how many commits had a preflight first, and how many of those held a changed file in the preflight's top five; `--from-commit [REV]` records a commit's changed files against the last preflight before it |
| `mimry roots` | List indexed folders on this machine (`--prune` forgets deleted ones) |
| `mimry plan ...` | Store and render explicit plan trees |
| `mimry mcp` | Run the MCP server (same as `mimry-mcp`) |
| `mimry adapters` | List the file-type adapters |
| `mimry install` / `mimry uninstall` | Manage agent skills |
| `mimry digest` | Print a canonical digest of the index |
| `mimry report` | Print the graph report, with its Suggested Questions |
| `mimry export --format {html,graphml,cypher,obsidian}` | Write the graph as an HTML viewer, GraphML, Cypher, or an Obsidian vault |
| `mimry cache wipe --current` | Delete this repo's cached index |

Use `mimry --root /path/to/repo <command>` to target another repo.

## Filtering and paging `find`

`find` applies every filter before pagination.
`--language` matches a file by its extension and by the languages of the symbols indexed in it, accepts extensions as names (`py`, `ts`, `rs`), counts `.tsx` files as TypeScript, and rejects names it does not know.
Path globs are case-sensitive and repository-relative.
`*` matches within one path segment, and `**` matches zero or more whole segments, so `*.py` matches only files at the root and `**/*.py` matches Python files at any depth.
The active scope is shown in CLI output, in the MCP `scope` field and in verbose ranking reasons.
Absolute or `..` globs, non-string filter values and invalid pagination values are rejected.

Pagination is opt-in: pass `--offset 0` (MCP `offset: 0`) for the first page, then raise `--offset` by `--limit` for each later page.
Without `--offset`, `find` keeps its compact ranking, which can differ from the first page of a paginated search.
MCP `scope` reports the normalized `offset` and whether pagination was requested.

## Diagnosing an install with `doctor`

`mimry doctor` checks that the installed code matches its package, that its dependencies and parsers work, and that the repo's index and caches are intact.
It only reads: it never sets up, indexes, repairs or installs anything, and it uses no network.
Each problem comes with the command that fixes it.
It exits 0 when everything is healthy, 1 when the repo is not set up or its index is out of date, and 2 when the install, a parser, the root or the index is broken.

## Keeping an index current

```text
$ mimry status
! my-app is out of date - 1 file changed, 1 new since the last index
    changed  src/auth/session.py
    new      src/auth/magic_link.py
  Indexed 5 minutes ago · at commit d7bf046
  3,105 files · 6,859 symbols · 10,749 links
  Languages: Python 1,840, TypeScript 610, Markdown 300, Go 150, Rust 90, Java 45, and 4 more
! The index is behind the working tree - 1 file changed, 1 new. The next query updates it; `mimry reindex` updates it now.

$ mimry reindex
✓ Updated the index for my-app in 2.2s
  1 file changed, 1 added
    changed  src/auth/session.py
    added    src/auth/magic_link.py
  3,106 files · 6,871 symbols · 10,762 links
```

Query commands check the index first.
When files changed since the last index, `find`, `explain`, `path`, `why` and the other lookups update it before they answer, and print `Reindexed first`.
`mimry reindex` is for updating the index ahead of time, not a step you must run after each change.

## Output

Every command answers the same way: one line that says what happened, a few indented details, and a next step only when something needs doing.
Add `--verbose` (`-v`) to any command for paths, scores, and raw ranking reasons.
Colour is used only on an interactive terminal, and `NO_COLOR=1` turns it off.
Piped and captured output always uses plain ASCII marks, so scripts and agents see the same text on every platform.

## Recursive plan trees

`mimry plan` stores explicit, user-authored plan trees: one root with ordered children, each of which can be split again.
It stores and renders the tree only; it does not generate plans, execute work, or track progress.

```bash
mimry plan new "Ship feature" --name ship-feature
mimry plan split <plan-id> <root-node-id> --child "Design" --child "Implement"
mimry plan tree <plan-id>          # also --json or --md
mimry plan check <plan-id>
mimry plan list
```

```text
Ship feature [node-...]
|-- Design [node-...]
`-- Implement [node-...]
```

`split` works on leaves only, and repeated `--child` arguments set sibling order.
Trees are canonical versioned JSON under `.mimry/plans/`, written atomically.
IDs, projections, and the semantic SHA-256 from `mimry plan digest` exclude paths, timestamps, locale, and formatting.
With `MIMRY_MCP_TOOLS=all` (see the README's MCP setup), MCP exposes read-only parity (`mimry_plan_tree`, `mimry_plan_check`, `mimry_plan_digest`, `mimry_plan_list`); changing a plan is CLI-only.
