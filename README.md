<p align="center">
  <img src="https://raw.githubusercontent.com/jbacalso24/mimry/main/assets/banner.png" alt="MIMRY - Local repo memory for coding agents" width="100%">
</p>

<!-- mcp-name: io.github.jbacalso24/mimry -->

<p align="center">
  <a href="https://pypi.org/project/mimry/"><img alt="PyPI" src="https://img.shields.io/pypi/v/mimry"></a>
  <a href="https://github.com/jbacalso24/mimry/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/jbacalso24/mimry/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-blue">
  <a href="LICENSE"><img alt="License MIT" src="https://img.shields.io/badge/license-MIT-green"></a>
  <a href="https://jbacalso24.github.io/mimry/"><img alt="Website" src="https://img.shields.io/badge/website-mimry-0b7285"></a>
</p>

<p align="center">
  <b>Cut the tokens your coding agent spends reading code by 48% to 67%.</b> <a href="#benchmark">See the benchmark</a>
</p>

MIMRY indexes your repository on your machine and tells your AI coding agent which files to read for a task, and which lines in them to read first.
Instead of opening ten files to find the right one, your agent goes straight to `sessions.py`, lines 233-247.

- **Local and deterministic.** Code is parsed with tree-sitter. No LLM, no API key, and nothing leaves your machine.
- **Works with any agent.** It's a CLI, so anything that can run a command can use it. It also ships an MCP server.
- **Gets better with use.** `mimry feedback` records which files helped, and similar tasks rank them higher.

<p align="center">
  <img src="https://raw.githubusercontent.com/jbacalso24/mimry/main/assets/demo.gif" alt="mimry preflight in a terminal: it ranks the Flask files for a session cookie bug and names the lines to read first" width="100%">
</p>

**Get started** (1 minute):

```bash
uv tool install mimry                   # install the CLI (or: pipx install mimry)
mimry install --platform claude-code    # teach your agent to use it
```

Then, in your project:

```bash
mimry preflight "Fix session cookie not refreshed when SESSION_REFRESH_EACH_REQUEST is set"
```

That's it. The first run builds the index, and your agent gets the files to read, why each one ranked, and where to start:

```text
Start with
   1  src/flask/sessions.py
      name matches · path matches · connected to other matches
      read first: lines 202-206 (get_cookie_secure), 233-247 (should_set_cookie)
   2  src/flask/ctx.py
      name matches · connected to other matches
      read first: lines 154-206 (copy_current_request_context), 395-403 (session)
```

The full context pack lands in `.mimry/mimry-out/context/latest.md`: matched symbols, how the files connect, likely edit points, and the project's test commands.

**Works in** Claude Code, Codex, GitHub Copilot CLI, Gemini CLI, OpenCode, Kiro, Amp and more. [Pick your agent](#connect-your-agent)

---

## Commands

```bash
mimry preflight "<task>"            # start a task: files and lines to read first
mimry find "<query>"                # rank files for a query
mimry symbol <name>                 # where a function, class or heading is defined
mimry related "<query>"             # files connected through imports and calls
mimry explain "<task>"              # files, key symbols, and how they connect
mimry why <file> --query "<task>"   # why a file ranked
mimry feedback ...                  # record which files helped
mimry status                        # is the index up to date?
mimry doctor                        # check the install and the index
```

Lookups reindex changed files on their own, so answers are never stale.
Every command and flag: [docs/commands.md](docs/commands.md).

## Benchmark

We replayed 92 real pull requests from 12 open-source projects and counted the tokens an agent read to find the code each one changed.

| How the agent reads files | Tokens saved with MIMRY |
|---|---|
| Whole files | **50%** |
| `grep -n` plus a few lines around each match | **48%** |
| Only the lines MIMRY points to first | **67%** |

Method, per-task results and how to reproduce it: [benchmarks/tokens](benchmarks/tokens/README.md).

## Connect your agent

```bash
mimry install --platform <agent>    # skill + MCP server, for every project
mimry install --list-platforms      # claude-code, codex, opencode, kilo, gemini, copilot, kiro, amp, devin, ...
```

| Flag | What it does |
|---|---|
| `--project` | Install into this repo only |
| `--always-on` | Add a short rules block to `AGENTS.md` or the agent's equivalent |
| `--hooks` | Point the agent to `mimry find` when it starts a broad grep |
| `--no-mcp` | Install the skill without registering the MCP server |

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

`uvx mimry mcp` runs the server straight from PyPI.
MIMRY is in the [official MCP Registry](https://registry.modelcontextprotocol.io/) as `io.github.jbacalso24/mimry`.
The tools mirror the commands: `mimry_preflight`, `mimry_find`, `mimry_symbol`, `mimry_related`, `mimry_explain`, `mimry_why`, `mimry_feedback` and more.

</details>

## Configuration

Commit a `mimry.toml` so your whole team indexes the same files:

```toml
[index]
include = ["src/**", "docs/**"]            # only these paths
exclude = ["**/fixtures/**", "legacy/**"]  # never these
max_file_bytes = 2_000_000                 # default is 1 MB
```

## How it works

MIMRY parses 16 languages, plus Markdown and office documents, into symbols and a graph of imports, calls and inheritance.
For a task it ranks files by name, path, content, symbol and graph matches.
Imports it can't resolve are dropped, never guessed.
More in [docs/how-it-works.md](docs/how-it-works.md).

## Privacy

Everything runs locally, and the index lives in your user cache directory, outside the repo.
Credential files are skipped, secret-looking values are redacted from feedback, and untracked files your `.gitignore` names are never indexed.

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).
MIMRY is in public alpha, so interfaces may change before 1.0.

## License

[MIT](LICENSE)
