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
| `mimry path <a> <b>` | Shortest relationship path between two files or symbols (`--json` for tools) |
| `mimry callers <name>` | Functions that call a function, from the graph (`--depth` follows the chain up to 3 calls, `--file` picks one of several, `--json` for tools) |
| `mimry callees <name>` | Functions that a function calls, with the same options |
| `mimry impact <name-or-path>` | What a change touches: callers by depth, the files that import it, and the tests to run (`--changed` checks the working tree against HEAD, `--depth` follows up to 5 calls, `--file` picks one of several, `--json` for tools) |
| `mimry why <file> --query "<task>"` | Why a file ranks for a query |
| `mimry route "<task>"` | Recommend an agent role (`--json` for tools) |
| `mimry brief "<task>" --agent <role>` | Write a role-specific brief |
| `mimry init` / `mimry index` | Set up a repo and build the index |
| `mimry reindex` / `mimry refresh` | Update the index ahead of time (`--full` re-parses everything); query commands index a repo that was never indexed, update a stale index, and rebuild missing graph files on their own |
| `mimry status` | Is the index current? (`--verify` re-hashes every file) |
| `mimry doctor` | Read-only install, parser, index, and cache diagnostics (`--json` for tools); common problems and fixes are in [troubleshooting](troubleshooting.md) |
| `mimry watch` | Poll for changes and refresh when they settle |
| `mimry git-hooks` | Keep the index fresh with git hooks (`install`/`uninstall`/`status`); the post-commit hook also records feedback from the commit, and re-running `mimry git-hooks` upgrades existing hooks |
| `mimry feedback ...` | Record what helped; `feedback stats`, `list`, `show <id>` read it back; `feedback stats` also shows, for the last 30 days, how many preflights ran, how many commits had a preflight first, and how many of those held a changed file in the preflight's top five; `--from-commit [REV]` records a commit's changed files against the last preflight before it |
| `mimry roots` | List indexed folders on this machine (`--prune` forgets deleted ones) |
| `mimry plan ...` | Store and render explicit plan trees |
| `mimry mcp` | Run the MCP server (same as `mimry-mcp`) |
| `mimry adapters` | List the file-type adapters |
| `mimry install` / `mimry uninstall` | Manage agent skills; `mimry install --refresh` rewrites the installed ones to this version |
| `mimry digest` | Print a canonical digest of the index |
| `mimry bench` | Replay this repo's recent merged pull requests and score how well MIMRY finds the files each one changed (`--tasks N`, `--json`, `--keep`; local only, no network or model calls) |
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

## Refreshing installed skills

`mimry install --refresh` rewrites the MIMRY skills that are already installed, so they match the version you are running.
It covers every platform in your user profile and, when the current directory is inside a Git repository, that repository's project install.
For each install it rewrites the skill and its references, plus the always-on rules and hooks where those were added.
It installs nothing new: a platform without a skill, or a missing always-on rule or hook, stays missing.
It does not register MCP servers or change Git's global ignore file, and a second run writes nothing.

`mimry upgrade` runs the refresh with the new version after a successful upgrade, so usually you do not need to run it yourself.
`--refresh` cannot be combined with `--platform`, `--project`, `--status`, `--dry-run`, `--list-platforms`, `--always-on` or `--hooks`.

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

## Exit codes

Scripts and hooks can tell an answer from a miss by the exit code.

| Situation | Exit code |
| --- | --- |
| Answer found | 0 |
| `find`, `related` or `semantic` with a real query and no matches | 1 |
| A named target that does not exist: `symbol`, `callers`, `callees`, `impact`, `why`, or a `path` source or target that looks like a file path and is not indexed | 1 |
| Empty or whitespace-only task or query | 2 |
| Invalid argument values, such as a bad depth or outcome | 2 |

A name that several symbols share exits 0 and lists the candidates, so you can add `--file`.
`path` between two indexed names with no connection also exits 0, because that is an answer.
`status` keeps its own codes: 1 when MIMRY is not set up, 2 when the index is not current.
`hook-check` and `hook-refresh` always exit 0, so a hook never fails the agent's tool call.
With `--json`, the JSON is printed and the exit code is the same.

## Inferred edges

Each graph edge is either `EXTRACTED` or `INFERRED`.
`EXTRACTED` means MIMRY read the relation directly from the source, such as a call within the same file or to a function imported by an exact import.
`INFERRED` means MIMRY picked the target without an exact import to back it, such as a call through a package re-export or a method of an imported file.
`mimry path`, `mimry callers`, `mimry callees` and `mimry impact` add ` (inferred)` to an inferred row, and print a note when one is shown.
Extracted rows print exactly as before.
With `--json`, each result or step carries a `confidence` field set to `EXTRACTED` or `INFERRED`.
The MCP tools `mimry_symbol` (with `relations`), `mimry_path` and `mimry_impact` return the same field.
For `callers` and `callees` at depth 2 or 3, a result is `EXTRACTED` only when some shortest chain to it uses extracted edges throughout.

## Impact

`mimry impact` answers what breaks if a change lands, before you make it.
Give it a symbol name or a repo-relative file path, or pass `--changed` to check the symbols and files that differ from HEAD.
A file path covers every symbol defined in it.
`--changed` checks each edited symbol, whose line range overlaps a changed hunk, and each untracked file, whose symbols all count.
Document headings and SQL tables are not code, so they are never targets; a changed Markdown file still counts for importers.

The answer starts with a summary line, then the targets, the callers grouped by how many calls away they are, the files that import the target file directly, and the tests to run.
Test files are those among the callers or importers, and the test command is the project's own (`uv run pytest` runs with the test files appended).

```bash
mimry impact a
mimry impact app/chain.py --depth 2
mimry impact --changed --json
```

```text
a: 1 direct source caller, 2 source callers within 3 calls, 2 importing files, 1 test file.

Targets (1)
  a (app/chain.py:1)

Direct callers (1)
  b (app/chain.py:5)

Callers 2 calls away (1, +1 in tests)
  c (app/chain.py:9)

Importing files (2)
  app/importer.py
  tests/test_chain.py

Tests to run (1)
  uv run pytest tests/test_chain.py
```

Text output leaves out callers in test files, which already appear under `Tests to run`; their count is shown in each depth heading.

`--json` returns `targets`, `callers` (each with `name`, `kind`, `path`, `line`, `depth`, `confidence` and `test`, and including test callers), `importers`, `tests`, `test_command` and `summary`.
The MCP tool `mimry_impact(target, file, changed, depth, root)` returns the same payload.

## Benchmark your repository

`mimry bench` checks how well MIMRY finds the files your own changes touched.
It takes the last 20 merged pull requests on the first-parent history of HEAD (`--tasks` sets the count) and replays each one.
A merge commit's task is its branch title, which is the first non-empty line of its body.
A squash commit's task is its subject without ` (#N)`, plus the first paragraph of its body.
A first paragraph made only of commit bullets or trailers, such as `Co-authored-by:`, is left out.
The files the pull request modified are the answer, leaving out `CHANGELOG*` and lock files.
Files it added are not counted, because they do not exist at the base and MIMRY cannot rank them.
A pull request that touches more than 30 files, or modifies no file left after the exclusions, is skipped and counted.
For each task, MIMRY indexes the commit's base in a throwaway worktree with its own cache, then runs `mimry find` with the task text.
The run never touches your working tree or your index, and it makes no network or model calls.

Each task reports:

- **Files**: how many existing files the pull request modified.
- **First gold rank**: the best position of one of those files in the top 10, or `-` when none is there.
- **Recall@5**: the share of those files in the top 5.
- **Tokens**: an estimate of reading files in MIMRY's order, charged up to the last changed file in the top 10. It is `-` when none is in the top 10.
- **find ms**: the wall time of the `mimry find` process. It includes starting Python, not only the search.

The summary gives the mean recall@5 and recall@10, the median first gold rank (over tasks where a changed file was found), the share of tasks with a changed file at rank 1, the median tokens of those tasks, and the median and 95th-percentile find time, with process start included.

Read it with these limits in mind.

- Changed files are a proxy for "the files the task needed". A pull request can touch files that were not needed, and a needed file can be missing from its diff.
- Files the pull request adds are not counted at all, since MIMRY cannot rank a file that does not exist at the base.
- Tokens are an estimate at 4 bytes per token, charged for each file's first 2,000 lines at the base commit.
- Times depend on the machine, so compare runs made on the same one.
- The history needs merge commits or squash commits whose subject ends with `(#N)`. Otherwise the command says so and exits 0.

Pass `--keep` to keep the temporary checkout and print its path, which is useful for checking a task by hand.
Pass `--json` for every task and the summary as JSON.

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
