<p align="center">
  <img src="https://raw.githubusercontent.com/jbacalso24/mimry/main/assets/banner.png" alt="MIMRY - Local repo memory for coding agents" width="100%">
</p>

<h1 align="center">MIMRY</h1>

<!-- mcp-name: io.github.jbacalso24/mimry -->

<p align="center">
  A local code index that tells AI coding agents which files, and which lines, to read for a task.
</p>

<p align="center">
  <a href="https://pypi.org/project/mimry/"><img alt="PyPI" src="https://img.shields.io/pypi/v/mimry"></a>
  <a href="https://github.com/jbacalso24/mimry/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/jbacalso24/mimry/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-blue">
  <a href="LICENSE"><img alt="License MIT" src="https://img.shields.io/badge/license-MIT-green"></a>
  <a href="https://jbacalso24.github.io/mimry/"><img alt="Website" src="https://img.shields.io/badge/website-mimry-0b7285"></a>
</p>

MIMRY is a command-line tool.
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

## Quick start

Run `mimry preflight` in your project with a task description.
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

## Features

| Feature | Description |
|---|---|
| Ranked lookup | Ranks files by name, path, content, symbol and graph matches, and shows why each one ranked |
| Line ranges | Names the functions, classes or sections to read first in each file |
| Code graph | Imports, calls and inheritance across 16 languages, parsed with tree-sitter |
| Documents | Markdown, text, PDF, Word and Excel files are indexed alongside code |
| Incremental indexing | Re-reads only changed files, and the main lookups refresh the index before answering |
| Feedback | Files recorded as useful rank higher for similar tasks |
| MCP server | Every lookup is also available as an MCP tool |
| Local | No LLM, API key or telemetry, and your code never leaves your machine |

## Supported agents

`mimry install --platform <name>` installs a skill that teaches the agent when to use MIMRY, and registers the MCP server where the agent supports it.

| Agent | Platform name |
|---|---|
| Claude Code | `claude-code` |
| Codex | `codex` |
| GitHub Copilot CLI | `copilot` |
| Gemini CLI | `gemini` |
| OpenCode | `opencode` |
| Kilo Code | `kilo` |
| Kiro | `kiro` |
| Amp | `amp` |
| Devin | `devin` |
| Factory Droid | `droid` |
| Aider, Antigravity, CodeBuddy, Hermes, Kimi, OpenClaw, Pi Agent, Trae | see `mimry install --list-platforms` |

| Option | Effect |
|---|---|
| `--project` | Install into the current repository instead of for every project |
| `--always-on` | Add a short rules block to the agent's always-read file, such as `AGENTS.md` |
| `--hooks` | Suggest `mimry find` when the agent starts a broad grep |
| `--no-mcp` | Install the skill without registering the MCP server |

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
mimry preflight "<task>"            # files and lines to read first, plus a context pack
mimry find "<query>"                # rank files for a query
mimry symbol <name>                 # find where a function, class or heading is defined
mimry related "<query>"             # files connected through imports and calls
mimry explain "<task>"              # key files and symbols, and how they connect
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

1. **Index.** Each file is parsed into symbols and edges for imports, calls and inheritance. The index is stored in your user cache directory, outside the repository.
2. **Rank.** A task is matched against file names, paths, content, symbols and the graph. Imports that cannot be resolved are dropped rather than guessed.
3. **Refresh.** Like git, MIMRY compares file metadata and re-reads only files that changed.

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
