<p align="center">
  <img src="https://raw.githubusercontent.com/jbacalso24/mimry/main/assets/banner.png" alt="MIMRY - Local repo memory for coding agents" width="100%">
</p>

<h1 align="center">MIMRY</h1>

<!-- mcp-name: io.github.jbacalso24/mimry -->

<p align="center">
  <strong>Local repo memory for AI coding agents.</strong>
  <br />
  Your agent reads lines 233-247, not the whole repo.
</p>

<p align="center">
  <a href="https://pypi.org/project/mimry/"><img alt="PyPI" src="https://img.shields.io/pypi/v/mimry"></a>
  <a href="https://github.com/jbacalso24/mimry/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/jbacalso24/mimry/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-blue">
  <img alt="License MIT" src="https://img.shields.io/badge/license-MIT-green">
</p>

<p align="center">
  <a href="https://jbacalso24.github.io/mimry/"><strong>Website</strong></a>
  ·
  <a href="#install"><strong>Install</strong></a>
  ·
  <a href="#results"><strong>Results</strong></a>
  ·
  <a href="#for-ai-agents"><strong>For AI agents</strong></a>
  ·
  <a href="#connect-mimry-to-your-agent"><strong>Connect your agent</strong></a>
  ·
  <a href="#commands"><strong>Commands</strong></a>
</p>

---

MIMRY is a command-line tool that indexes a code repository on your machine and tells an AI coding agent which files to read for a task, and which lines in them to read first.
It works with Claude Code, Codex, GitHub Copilot CLI, Gemini CLI, OpenCode and any other agent that can run a command, and agents that speak MCP can also reach it through its built-in MCP server.
On 48 real pull requests from six open-source projects, a scripted agent that started with MIMRY read **58% to 69% fewer tokens** than the same agent searching the code by keyword, and found every file the change needed in **32 tasks instead of 21**.

<p align="center">
  <img src="assets/demo.gif" alt="mimry preflight in a terminal: it ranks the Flask files for a session cookie bug and names the lines to read first" width="100%">
</p>

## Why coding agents need it

- **Agents start every task cold.**
  They guess search terms, open whole files to see whether they matter, and pay for every line in tokens and context window.
- **MIMRY answers "where is the code for this?" up front.**
  One command, `mimry preflight "<task>"`, returns the files to read first, why each one ranked, and the functions, classes or sections inside them that match the task.
- **Fewer tokens, more room to think.**
  An agent that reads 15 lines instead of 400 spends less and keeps its context window for the code that matters.
- **It runs entirely on your machine.**
  No code is uploaded, there is no API key or remote embedding service, and there is no telemetry.
- **It points; the source decides.**
  MIMRY is not an LLM or a code generator.
  It narrows where to look, and source files, tests and build output stay the final truth.

## What your agent gets

```text
$ mimry preflight "Fix session cookie not refreshed when SESSION_REFRESH_EACH_REQUEST is set"
✓ Context ready for "Fix session cookie not refreshed when SESSION_REFRESH_EACH_REQUEST is set" (0.4s)
  Index is up to date (226 files, indexed 34 minutes ago)
  Context pack: .mimry/mimry-out/context/latest.md

Start with
   1  src/flask/sessions.py
      name matches · path matches · connected to other matches
      read first: lines 202-206 (get_cookie_secure), 233-247 (should_set_cookie)
   2  src/flask/ctx.py
      name matches · connected to other matches
      read first: lines 154-206 (copy_current_request_context), 395-403 (session)
   3  src/flask/testing.py
      name matches · connected to other matches
      read first: lines 135-183 (session_transaction), 193-202 (_request_from_builder_args)

Next: read .mimry/mimry-out/context/latest.md before opening files.
```

The context pack it writes adds, for each file, the symbols that matched, how the files connect, which ones are likely edit surfaces and which are support files, and the project's own test commands.
It uses relative paths and never copies whole source files or secret values.
See [`docs/context-packs.md`](docs/context-packs.md) for the format.

## Results

Each task is a real merged pull request, and the task text is its title.
Two scripted agents get the same task and the same budget of 10 file reads, and the count stops once the agent has read every source file the pull request changed.
"Without MIMRY" searches the code for the task's keywords and reads the files that match most; "with MIMRY" reads in the order the context pack gives.

| How the agent reads a file | Tokens without MIMRY | Tokens with MIMRY | Fewer tokens | Tasks located without | Tasks located with |
|---|---|---|---|---|---|
| The whole file | 5,660,661 | **2,388,654** | **57.8%** | 21 / 48 | **32 / 48** |
| `grep -n`, then a `sed` window around each match | 7,592,841 | **3,482,674** | **54.1%** | 19 / 48 | **30 / 48** |
| The lines MIMRY lists first, then the whole file if needed | 5,660,661 | **1,777,105** | **68.6%** | 21 / 48 | **32 / 48** |

- The 48 tasks come from rich, echo, axios, clap, Polly and guzzle (Python, Go, JavaScript, Rust, C# and PHP), picked by a fixed rule before any run.
- MIMRY was never tuned on them: each read mode was run once.
- Tokens are counted with tiktoken's `o200k_base` encoding; indexing is not charged, because it uses no model tokens.
- The agents are scripted, not language models, so the numbers measure where each search points, and a rerun gives the same counts.
- guzzle is the weak spot: there MIMRY located fewer tasks than keyword search.

Reproduce it (it clones the six repositories into the work directory):

```bash
git clone https://github.com/jbacalso24/mimry.git
cd mimry
uv run --with tiktoken python benchmarks/tokens/run.py --work ../mimry-bench --set holdout3 --reads ranges
```

The [token benchmark](benchmarks/tokens/README.md) has the full method, how tasks were selected, the other task sets, and per-repository and per-task results.
A separate retrieval benchmark runs in CI on every pull request: 94% of the files a task needs appear in MIMRY's top 5 results (recall@5 0.94, nDCG@5 0.84).

## Install

MIMRY is a Python package on [PyPI](https://pypi.org/project/mimry/) and needs Python 3.11 or newer.
It works on Windows, Linux, and macOS.

```bash
uv tool install mimry
```

`pipx install mimry` and `pip install mimry` also work.
The install provides two commands: `mimry` (the CLI) and `mimry-mcp` (the MCP server for agents).

Check it:

```bash
mimry --help
```

Upgrade later with `uv tool upgrade mimry`.
It updates the install in place and adds any new dependencies.
On Windows, close agent sessions that use MIMRY first: each one keeps `mimry-mcp` running, and Windows will not replace files a running program has open.
If `mimry status` reports that the install is incomplete, it prints the command that repairs it, usually `uv tool upgrade mimry`.

## Quickstart

Run these inside the project you want an agent to work on:

```bash
cd /path/to/your/project
mimry init
mimry index
mimry preflight "fix the login redirect bug"
```

`preflight` also sets MIMRY up and builds the index on first use, so in practice it is the only command an agent needs to start a task.

## For AI agents

This section is written for an AI coding agent working in a repository where MIMRY is installed.

### When to use MIMRY

Start every task with MIMRY.
It replaces exploring the repository by grep: guessing search terms, then reading files to see whether they matter.
Run it whenever you need to find where something lives, what a task touches, or how two parts of the code connect.
For an exact string you already know, such as an identifier or an error message, use `rg` directly, or `git grep` when `rg` is not installed.

### The workflow

1. **Start the task:** `mimry preflight "<task in plain words>"`.
2. **Read the context pack:** `.mimry/mimry-out/context/latest.md`.
   It lists ranked files with reasons, key symbols, relationships, risky paths, and verification commands.
   Where a file's functions, classes or sections match the task, it names their lines (`Read first: lines 120-168`), so the agent reads those before the rest of the file.
3. **Navigate with focused queries** instead of scanning the tree:
   `mimry find "<query>"`, `mimry symbol <name>`, `mimry related "<query>"`, `mimry explain "<task>"`, `mimry path <a> <b>`, `mimry why <file> --query "<task>"`.
4. **Verify against the source.**
   MIMRY output is navigation, not proof.
   Open the files, run the tests, and trust the build output over any ranking.
5. **Record what helped:**
   `mimry feedback --query "<task>" --opened <files> --changed <files> --verification "<command and result>" --outcome passed`.
   Future searches for similar tasks rank those files higher.

### Rules

- `preflight`, `context`, `brief` and `route` reindex first when files changed or MIMRY was upgraded, so a task never starts from a stale index.
- If a quick lookup such as `find` or `symbol` says the index is out of date, run `mimry reindex` (fast: it only re-reads changed files).
- Treat `.mimry/`, caches, and generated files as support artifacts, never as the place to fix a bug.
- Never paste secret values into context or reports; MIMRY skips secret-looking files and redacts feedback.
- Output is plain ASCII when piped (`OK`, `!`, `x` marks), and exit codes are meaningful: `0` ok, `1` not set up, `2` stale or an error.
  `mimry route "<task>" --json` returns structured data.

## Connect MIMRY to your agent

MIMRY is a CLI first: any agent that can run a shell command can run `mimry preflight` and the commands below.
It also ships an MCP server, so agents that support MCP can call it as a tool (stdio transport, command `mimry-mcp`).
Each agent starts its own `mimry-mcp` when a session begins and stops it when the session ends.
You never run `mimry-mcp` yourself: after `mimry install --platform <agent>`, the agent takes care of it.
`mimry install --platform <agent>` registers it for you at user scope, so every project gets it: through the agent's own CLI for Claude Code, Codex, Gemini CLI, Factory Droid, Kimi, Copilot CLI, Amp and Devin, and in the config file for Kiro and OpenCode.
For other agents it prints the snippet to add in their MCP settings.
Pass `--no-mcp`, or set `MIMRY_NO_MCP_REGISTRATION=1`, to skip it; a global `mimry uninstall --platform <agent>` removes it.

To register it by hand instead:

Claude Code:

```bash
claude mcp add --scope user mimry -- mimry-mcp
```

Codex:

```bash
codex mcp add mimry -- mimry-mcp
```

Any other MCP client:

```json
{
  "mcpServers": {
    "mimry": { "command": "mimry-mcp" }
  }
}
```

Without installing anything first, `uvx mimry mcp` runs the same server straight from PyPI (`"command": "uvx", "args": ["mimry", "mcp"]`).
MIMRY is listed in the [official MCP Registry](https://registry.modelcontextprotocol.io/) as `io.github.jbacalso24/mimry`.

The server works on its current directory by default, and every tool accepts a `root` argument for another repo.

| MCP tool | What it does |
|---|---|
| `mimry_preflight` | Set up if needed, then write a context pack and return the top files for a task |
| `mimry_find` | Rank files for a query, with the line ranges to read first in each |
| `mimry_related` | Files related to a query through the graph |
| `mimry_semantic` | Fuzzy "something like this" search |
| `mimry_symbol` | Look up a function, class, or other symbol by name |
| `mimry_context` | Write a context pack for a task |
| `mimry_explain` | Relevant files, key symbols, and how they connect |
| `mimry_path` | Shortest relationship path between two files or symbols |
| `mimry_why` | Why a file ranks for a query |
| `mimry_route` / `mimry_brief` | Recommend an agent role and write a role-specific brief |
| `mimry_status` / `mimry_refresh` / `mimry_reindex` / `mimry_init` | Index health and maintenance |
| `mimry_feedback` | Record which files helped a task |
| `mimry_list_adapters`, `mimry_digest`, `mimry_plan_*` | Adapters, canonical index digest, read-only plan trees |

### Teach your agent to use it

MIMRY can install a skill (instruction bundle) that teaches an agent the workflow above:

```bash
mimry install --project --platform claude-code --always-on --hooks
```

- `--project` installs into this repo only; omit it to install for every project.
- `--always-on` adds a short rules block to the agent's always-read file, such as `AGENTS.md`.
- `--hooks` adds a hook that reminds the agent once per session, on its first search, that MIMRY is available.

Supported platforms: `claude-code`, `codex`, `opencode`, `kilo`, `aider`, `copilot`, `claw`, `droid`, `trae`, `trae-cn`, `hermes`, `kiro`, `gemini`, `agents`, `amp`, `devin`, `antigravity`, `kimi`, `pi`, `codebuddy`.
Run `mimry install --list-platforms` for aliases, `mimry install --project --platform <name> --status` to check an install, and `mimry uninstall --project --platform <name> --always-on --hooks` to remove it.

## Commands

Add `--verbose` (`-v`) to any command for paths, scores, and raw ranking reasons.

| Command | Use it to |
|---|---|
| `mimry preflight "<task>"` | Start a task: context pack plus the files to read first |
| `mimry find "<query>"` | Rank files, with the lines to read first; repeat `--language`, `--path-glob`, or `--exclude-glob`, and page with `--offset`/`--limit` (`--semantic` blends fuzzy matches; `--snippets` adds bounded, live-source excerpts) |
| `mimry related "<query>"` | Find files connected through the graph |
| `mimry semantic "<query>"` | Fuzzy recall for "I remember something like..." |
| `mimry symbol <name>` | Find where a symbol or Markdown heading is defined, or a file by name |
| `mimry context "<task>"` | Write a context pack without the preflight checks |
| `mimry explain "<task>"` | Files, key symbols, and how they connect |
| `mimry path <a> <b>` | Shortest relationship path between two files or symbols |
| `mimry why <file> --query "<task>"` | Why a file ranks for a query |
| `mimry route "<task>"` | Recommend an agent role (`--json` for tools) |
| `mimry brief "<task>" --agent <role>` | Write a role-specific brief |
| `mimry init` / `mimry index` | Set up a repo and build the index |
| `mimry reindex` / `mimry refresh` | Update the index (`--full` re-parses everything) |
| `mimry status` | Is the index current? (`--verify` re-hashes every file) |
| `mimry doctor` | Read-only install, parser, index, and cache diagnostics (`--json` for tools) |
| `mimry watch` | Poll for changes and refresh when they settle |
| `mimry git-hooks` | Keep the index fresh with git hooks (`install`/`uninstall`/`status`) |
| `mimry feedback ...` | Record what helped; `feedback stats`, `list`, `show <id>` read it back |
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
Filters, pagination, `doctor`, and plan trees are covered in [`docs/commands.md`](docs/commands.md).

## How it works

1. **Index.**
   MIMRY parses each source file into symbols and a relationship graph: imports, calls, definitions, inheritance and references, across 16 programming languages plus Markdown, text and office documents.
   It adds full-text and local semantic search on top.
   The index lives in your user cache directory, outside the repo.
2. **Rank.**
   For a task, it combines name, path, content, graph and symbol matches, and says why each file ranked.
   Imports it cannot resolve are dropped rather than guessed, so every relationship it shows is real.
3. **Stay fresh.**
   Like git, it trusts unchanged file metadata, so a reindex re-reads only the files that changed, and `preflight` reindexes first when anything did.
4. **Learn.**
   `mimry feedback` records which files an agent actually used, and similar future tasks rank them higher.

Details are in [`docs/how-it-works.md`](docs/how-it-works.md): what it writes, the graph engine, freshness, feedback, cache safety and adapters.

## Privacy

- Everything runs locally: no code is uploaded and there is no telemetry.
- Semantic search uses a deterministic local method (`local-hash-v1`), not a remote embedding service.
- Common credential files are skipped, and secret-looking values are redacted from feedback before it is stored.
- In a git repository, untracked files the `.gitignore` names are not indexed; tracked files always are.
- Tester-owned `acceptance_tests/` trees stay out of ordinary results by default.
  This is cooperative isolation, not secrecy: anyone with the repo can still open those files.

## Supported platforms

MIMRY runs on Windows, Linux, and macOS with Python 3.11 to 3.14.
CI runs the full suite on all three operating systems with Python 3.11, plus Python 3.14 on Linux.
See [platform details](docs/how-it-works.md#platform-details) for the cross-platform behaviour it handles explicitly.

## Support and releases

CI runs on every pull request and every push to `main`: lint, tests, an end-to-end determinism matrix, an unlocked extracted-sdist parser suite, and a clean-wheel smoke test on each platform, plus the retrieval benchmark gate on Linux.
A separate workflow audits the locked runtime dependencies for known vulnerabilities whenever they change, and weekly.
Releases are cut by pushing a `vX.Y.Z` tag, which builds reproducible artifacts and publishes them to PyPI and GitHub Releases.
See [`CHANGELOG.md`](CHANGELOG.md) for release notes and [`RELEASING.md`](RELEASING.md) for the release checks.
Report security issues privately as described in [`SECURITY.md`](SECURITY.md).

## Contributing

Contributions are welcome: bug fixes, portability and privacy improvements, documentation, and retrieval changes backed by the benchmark.
Open an issue first for large features or changes to privacy defaults.
See [`CONTRIBUTING.md`](CONTRIBUTING.md) for setup and the checks to run before opening a pull request.

## Development

```bash
git clone https://github.com/jbacalso24/mimry.git
cd mimry
uv sync
uv run ruff format .
uv run ruff check .
uv run pytest -q
```

Install the CLI from your checkout, so edits take effect immediately:

```bash
uv tool install --editable .
```

An editable install picks up code changes, but not dependency changes.
After a pull that changes the dependencies in `pyproject.toml`, run `uv tool upgrade mimry` to install them; `mimry status` names any that are missing.
Do not reinstall with `uv tool install --force` instead: it deletes the whole tool environment first, and on Windows, while any `mimry-mcp` is running, the delete stops at the first locked file and leaves a half-removed install.

Install the Git hooks with `uv run pre-commit install`.
Run `uv run mimry-integration-smoke` for a real MCP stdio round trip plus isolated Claude Code and Codex registration checks; it never touches your normal agent configuration.

## Status

MIMRY is a public alpha: early but usable for local, per-project agent memory, and its interfaces may still change before 1.0.
It is not a whole-computer brain and should not be pointed at entire drives or home directories.

## License

MIMRY is free and open source under the [MIT License](LICENSE).
Feel free to use it in your own projects, personal or commercial, and to modify, fork or build on it.
Keep the copyright and license notice when you redistribute it.
