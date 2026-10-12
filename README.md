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
  <a href="#workflow"><strong>Workflow</strong></a>
  ·
  <a href="#results"><strong>Results</strong></a>
  ·
  <a href="#connect-your-agent"><strong>Connect your agent</strong></a>
  ·
  <a href="#commands"><strong>Commands</strong></a>
</p>

---

MIMRY is a command-line tool that indexes your repository locally and tells an AI coding agent which files, and which lines in them, to read for a task.
It works with any agent that can run a command, and ships an MCP server for agents that speak MCP.

On 92 real pull requests from 12 open-source projects, an agent that started with MIMRY read **48% to 67% fewer tokens** than one searching by keyword, and found every file the change needed in **65 tasks instead of 49**.

<p align="center">
  <img src="https://raw.githubusercontent.com/jbacalso24/mimry/main/assets/demo.gif" alt="mimry preflight in a terminal: it ranks the Flask files for a session cookie bug and names the lines to read first" width="100%">
</p>

## Why

- **Agents start every task cold.** They guess search terms and open whole files to see whether they matter.
- **MIMRY answers "where is the code for this?" up front**, with the files to read, why each ranked, and the lines to start with.
- **Fewer tokens, more room to think.** Reading 15 lines instead of 400 saves money and context window.
- **Local only.** No code is uploaded, no API key, no telemetry.
- **It points; the source decides.** MIMRY is not an LLM. Tests and build output stay the final truth.

## Install

Python 3.11 or newer, on Windows, Linux or macOS.

```bash
uv tool install mimry
```

`pipx install mimry` and `pip install mimry` also work.
Upgrade with `uv tool upgrade mimry` (on Windows, close agent sessions that use MIMRY first).

## Workflow

Inside your project:

```bash
mimry preflight "fix the login redirect bug"
```

The first run sets MIMRY up and builds the index; every run writes a context pack to `.mimry/mimry-out/context/latest.md`.

1. **Start** with `mimry preflight "<task>"`.
2. **Read the context pack.** It ranks the files, says why, and names the lines to read first (`Read first: lines 120-168`).
3. **Navigate** with `mimry find`, `symbol`, `related`, `explain`, `path` or `why` instead of scanning the tree.
4. **Verify against the source.** MIMRY output is navigation, not proof.
5. **Record what helped** with `mimry feedback`, so similar tasks rank those files higher.

For an exact string you already know, such as an identifier or an error message, `rg` is still the right tool.
The lookups reindex changed files on their own, and a new git worktree gets its own index on first use.

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

The context pack adds the matched symbols, how the files connect, likely edit surfaces, and the project's test commands.
It never copies whole source files or secrets; see [`docs/context-packs.md`](docs/context-packs.md).

## Results

Each task is a real merged pull request, with its title as the task.
Two scripted agents get the same task and 10 file reads: one searches by keyword, the other reads in MIMRY's order.

| How the agent reads a file | Tokens, without MIMRY | Tokens, with MIMRY | Saved | Located, without | Located, with |
|---|---|---|---|---|---|
| The whole file | 8,019,505 | **4,010,701** | **50.0%** | 49 / 92 | **65 / 92** |
| `grep -n`, then a `sed` window around each match | 10,038,399 | **5,260,194** | **47.6%** | 46 / 92 | **61 / 92** |
| The lines MIMRY lists first, then the whole file if needed | 8,019,505 | **2,635,374** | **67.1%** | 49 / 92 | **65 / 92** |

- Two frozen task sets in Python, Go, TypeScript, JavaScript, Rust, Java, C# and PHP, picked before any run and never used for tuning.
- The agents are scripted, so a rerun gives the same counts. Tokens are counted with tiktoken's `o200k_base`.
- MIMRY does not win everywhere: in guzzle it located fewer tasks, and in retrofit it read more tokens.

Reproduce one set from a clone of this repository:

```bash
uv run --with tiktoken python benchmarks/tokens/run.py --work ../mimry-bench --set holdout3 --reads ranges
```

The [token benchmark](benchmarks/tokens/README.md) has the full method and per-task results.
CI also runs a retrieval benchmark on every pull request (recall@5 0.94, nDCG@5 0.84).

## Connect your agent

Any agent that can run a shell command can use MIMRY directly.
To also register the MCP server and install a skill that teaches the workflow:

```bash
mimry install --platform claude-code
```

- Platforms include `claude-code`, `codex`, `opencode`, `kilo`, `gemini`, `copilot`, `kiro`, `amp` and `devin`; `mimry install --list-platforms` shows them all.
- `--project` installs into this repo only, `--always-on` adds a rules block to `AGENTS.md` or similar, and `--hooks` points the agent to `mimry find` when it starts a broad grep.
- `--no-mcp` skips the MCP registration, and `mimry uninstall --platform <name>` removes the install.

<details>
<summary>Register the MCP server by hand</summary>

```bash
claude mcp add --scope user mimry -- mimry-mcp   # Claude Code
codex mcp add mimry -- mimry-mcp                 # Codex
```

Any other MCP client:

```json
{
  "mcpServers": {
    "mimry": { "command": "mimry-mcp" }
  }
}
```

`uvx mimry mcp` runs the server straight from PyPI without installing it.
MIMRY is in the [official MCP Registry](https://registry.modelcontextprotocol.io/) as `io.github.jbacalso24/mimry`.
The agent starts and stops `mimry-mcp` itself, and every tool accepts a `root` argument for another repo.

| MCP tool | What it does |
|---|---|
| `mimry_preflight` | Set up if needed, write a context pack and return the top files |
| `mimry_find` | Rank files for a query, with the lines to read first |
| `mimry_related` / `mimry_semantic` | Files related through the graph / fuzzy search |
| `mimry_symbol` | Look up a symbol by name |
| `mimry_context` / `mimry_explain` | Write a context pack / files, symbols and how they connect |
| `mimry_path` / `mimry_why` | Relationship path between two items / why a file ranks |
| `mimry_route` / `mimry_brief` | Recommend an agent role / write a role brief |
| `mimry_status` / `mimry_refresh` / `mimry_reindex` / `mimry_init` | Index health and maintenance |
| `mimry_feedback` | Record which files helped |
| `mimry_list_adapters`, `mimry_digest`, `mimry_plan_*` | Adapters, index digest, read-only plan trees |

</details>

## Commands

| Command | Use it to |
|---|---|
| `mimry preflight "<task>"` | Start a task: context pack plus the files to read first |
| `mimry find "<query>"` | Rank files, with the lines to read first |
| `mimry symbol <name>` | Find where a symbol or heading is defined |
| `mimry related "<query>"` | Find files connected through the graph |
| `mimry explain "<task>"` | Show files, key symbols, and how they connect |
| `mimry why <file> --query "<task>"` | See why a file ranks |
| `mimry status` / `mimry reindex` | Check and update the index |
| `mimry feedback ...` | Record what helped |
| `mimry doctor` | Diagnose the install and the index |

Every command, flag and filter is in [`docs/commands.md`](docs/commands.md).

## Choosing which files to index

Commit a `mimry.toml` at the root so the whole team indexes the same files:

```toml
[index]
include = ["src/**", "docs/**"]            # optional: only these paths
exclude = ["**/fixtures/**", "legacy/**"]  # optional: never these (wins over include)
max_file_bytes = 2_000_000                 # optional: replaces the 1 MB limit
```

An invalid file fails with an error naming `mimry.toml`, and changing the file marks the index out of date.

## How it works

MIMRY parses 16 programming languages plus Markdown and office documents into symbols and a graph of imports, calls and inheritance.
It ranks files for a task by name, path, content, graph and symbol matches, and drops imports it cannot resolve instead of guessing.
Like git, it re-reads only the files whose metadata changed.
Details are in [`docs/how-it-works.md`](docs/how-it-works.md).

## Privacy

- Everything runs locally, and the index lives in your user cache directory, outside the repo.
- Semantic search is a deterministic local method, not a remote embedding service.
- Credential files are skipped, secrets are redacted from feedback, and untracked files your `.gitignore` names are not indexed.

## Contributing

Contributions are welcome; open an issue first for large features or privacy changes.
See [`CONTRIBUTING.md`](CONTRIBUTING.md) for setup and checks, [`SECURITY.md`](SECURITY.md) to report a vulnerability, and [`CHANGELOG.md`](CHANGELOG.md) for releases.

MIMRY is a public alpha: usable today, with interfaces that may change before 1.0.

## License

[MIT](LICENSE)
