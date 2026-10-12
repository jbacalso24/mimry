<p align="center">
  <img src="https://raw.githubusercontent.com/jbacalso24/mimry/main/assets/banner.png" alt="MIMRY - Local repo memory for coding agents" width="100%">
</p>

<h1 align="center">MIMRY</h1>

<!-- mcp-name: io.github.jbacalso24/mimry -->

<p align="center">
  Local repo memory for AI coding agents.
</p>

<p align="center">
  <a href="https://pypi.org/project/mimry/"><img alt="PyPI" src="https://img.shields.io/pypi/v/mimry"></a>
  <a href="https://github.com/jbacalso24/mimry/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/jbacalso24/mimry/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-blue">
  <a href="LICENSE"><img alt="License MIT" src="https://img.shields.io/badge/license-MIT-green"></a>
  <a href="https://jbacalso24.github.io/mimry/"><img alt="Website" src="https://img.shields.io/badge/website-mimry-0b7285"></a>
</p>

MIMRY is a command-line tool that tells AI coding agents which files, and which lines in them, to read for a task.
It parses a repository into symbols and a dependency graph, keeps the index on your machine, and ranks the files that matter for a task described in plain words.
Agents call it from the shell or through its MCP server.

<p align="center">
  <img src="https://raw.githubusercontent.com/jbacalso24/mimry/main/assets/demo.gif" alt="mimry preflight in a terminal: it ranks the Flask files for a session cookie bug and names the lines to read first" width="100%">
</p>

## Installation

```bash
uv tool install mimry
```

Requires Python 3.11 or newer on Windows, Linux or macOS.
`pipx install mimry` and `pip install mimry` also work.

> [!NOTE]
> To upgrade, run `uv tool upgrade mimry`.
> On Windows, close agent sessions that use MIMRY first, because a running `mimry-mcp` keeps its files locked.

## Quick start

Run `mimry preflight` in your repository with a task description.
The first run builds the index.

```bash
mimry preflight "Fix session cookie not refreshed when SESSION_REFRESH_EACH_REQUEST is set"
```

Output on the Flask repository:

```text
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
```

The same result is written to `.mimry/mimry-out/context/latest.md` as a context pack for the agent, with the matched symbols, related files and the project's test commands.

To have your agent use MIMRY on its own, install it for that agent:

```bash
mimry install --platform claude-code
```

> [!TIP]
> Add `--always-on --hooks` so the agent reaches for MIMRY without being told: `--always-on` adds the rules to its always-read file, and `--hooks` suggests `mimry find` whenever it starts a broad grep.

## Features

| Feature | Description |
|---|---|
| Ranked lookup | Ranks files by name, path, content, symbol and graph matches, and shows why each one ranked |
| Line ranges | Names the functions, classes or sections to read first in each file |
| Code graph | Maps imports, calls and inheritance across 16 languages with tree-sitter |
| Documents | Indexes Markdown, text, PDF, Word and Excel files alongside code |
| Incremental indexing | Re-reads only changed files, and refreshes the index before the main lookups answer |
| Feedback | Ranks files recorded as useful higher for similar tasks |
| MCP server | Exposes every lookup as an MCP tool |
| Local | Runs without an LLM, API key or telemetry, and keeps your code on your machine |

## Supported agents

`mimry install --platform <name>` installs a skill that teaches the agent when to use MIMRY, and registers the MCP server where the agent supports it.

| Agent | Platform name | Skill | MCP server |
|---|---|---|---|
| Claude Code | `claude-code` | ✅ | ✅ |
| Codex | `codex` | ✅ | ✅ |
| GitHub Copilot CLI | `copilot` | ✅ | ✅ |
| Gemini CLI | `gemini` | ✅ | ✅ |
| OpenCode | `opencode` | ✅ | ✅ |
| Kiro | `kiro` | ✅ | ✅ |
| Amp | `amp` | ✅ | ✅ |
| Devin | `devin` | ✅ | ✅ |
| Factory Droid | `droid` | ✅ | ✅ |
| Kimi | `kimi` | ✅ | ✅ |
| Kilo Code | `kilo` | ✅ | [manual](#manual-mcp-setup) |
| Aider, Antigravity, CodeBuddy, Hermes, OpenClaw, Pi Agent, Trae | see `mimry install --list-platforms` | ✅ | [manual](#manual-mcp-setup) |
| Cursor | | 🚧 [#78](https://github.com/jbacalso24/mimry/issues/78) | 🚧 |
| GitHub Copilot in VS Code | | 🚧 [#79](https://github.com/jbacalso24/mimry/issues/79) | 🚧 |
| Windsurf, Cline, Roo Code, Zed, JetBrains | | 🚧 [#80](https://github.com/jbacalso24/mimry/issues/80) | 🚧 |
| Grok | | 🚧 [#93](https://github.com/jbacalso24/mimry/issues/93) | 🚧 |

✅ means `mimry install` sets it up, manual means you add the MCP server by hand, and 🚧 means support is in progress.

See [docs/agents.md](docs/agents.md) for where each agent's files go and how its MCP server is registered.

| Option | Description |
|---|---|
| `--project` | Install into the current repository only, instead of for every repository |
| `--always-on` | Add a short rules block to the agent's always-read file, such as `AGENTS.md` |
| `--hooks` | Suggest `mimry find` when the agent starts a broad grep |
| `--no-mcp` | Install the skill without registering the MCP server |

<a id="manual-mcp-setup"></a>
<details>
<summary>Manual MCP setup</summary>

```bash
claude mcp add --scope user mimry -- mimry-mcp   # Claude Code
codex mcp add mimry -- mimry-mcp                 # Codex
```

Other MCP clients:

```json
{
  "mcpServers": {
    "mimry": { "command": "mimry-mcp" }
  }
}
```

`uvx mimry mcp` runs the server without installing it.
MIMRY is listed in the [MCP Registry](https://registry.modelcontextprotocol.io/) as `io.github.jbacalso24/mimry`.

</details>

## Usage

```bash
mimry preflight "<task>"            # list the files and lines to read first, and write a context pack
mimry find "<query>"                # rank files for a query
mimry symbol <name>                 # find where a function, class or heading is defined
mimry related "<query>"             # list files connected through imports and calls
mimry explain "<task>"              # show the key files and symbols, and how they connect
mimry why <file> --query "<task>"   # explain why a file ranked
mimry feedback ...                  # record which files helped
mimry status                        # check whether the index is current
mimry doctor                        # diagnose the install and the index
```

See [docs/commands.md](docs/commands.md) for every command and option.

## Configuration

A `mimry.toml` at the repository root controls which files are indexed.
Commit it so everyone on the team gets the same index.

```toml
[index]
include = ["src/**", "docs/**"]            # index only these paths
exclude = ["**/fixtures/**", "legacy/**"]  # never index these paths
max_file_bytes = 2_000_000                 # size limit per file (default 1 MB)
```

## Benchmarks

Tokens an agent read before it found the code each pull request changed, measured on 92 merged pull requests from 12 open-source projects.

| How the agent reads files | Tokens saved with MIMRY |
|---|---|
| Whole files | **50%** |
| `grep -n`, then a few lines around each match | **48%** |
| The line ranges MIMRY names first | **67%** |

See [benchmarks/tokens](benchmarks/tokens/README.md) for the method, per-project results and how to reproduce them.

## How it works

1. **Index.** MIMRY parses each file into symbols and edges for imports, calls and inheritance, and stores the index in your user cache directory, outside the repository.
2. **Rank.** MIMRY matches a task against file names, paths, content, symbols and the graph, and drops imports it cannot resolve rather than guessing.
3. **Refresh.** Like Git, MIMRY compares file metadata and re-reads only the files that changed.

See [docs/how-it-works.md](docs/how-it-works.md) for details.

## Privacy

Indexing and search run locally.
Credential files are skipped, secret-looking values are redacted from feedback, and untracked files named in `.gitignore` are not indexed.

## Contributing

Bug reports and pull requests are welcome.
See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and checks.
MIMRY is in public alpha, and its interfaces may change before 1.0.

## License

[MIT](LICENSE)
