<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/jbacalso24/mimry/main/assets/logo-dark.svg">
    <img src="https://raw.githubusercontent.com/jbacalso24/mimry/main/assets/logo.svg" alt="MIMRY" width="240">
  </picture>
</p>

<!-- mcp-name: io.github.jbacalso24/mimry -->

<p align="center">
  Finds the files a coding task touches, so your agent reads less.
</p>

<p align="center">
  <a href="#benchmarks">71% fewer tokens to reach the code a task changes, in a scripted retrieval benchmark.</a>
</p>

<p align="center">
  <a href="https://pypi.org/project/mimry/"><img alt="PyPI" src="https://img.shields.io/pypi/v/mimry"></a>
  <a href="https://github.com/jbacalso24/mimry"><img alt="GitHub stars" src="https://img.shields.io/github/stars/jbacalso24/mimry"></a>
  <a href="https://github.com/jbacalso24/mimry/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/jbacalso24/mimry/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-blue">
  <a href="LICENSE"><img alt="License MIT" src="https://img.shields.io/badge/license-MIT-green"></a>
  <a href="https://jbacalso24.github.io/mimry/"><img alt="Website" src="https://img.shields.io/badge/website-mimry-0b7285"></a>
</p>

<p align="center">
  If <a href="https://github.com/jbacalso24/mimry">MIMRY</a> saves your agent tokens, please star it.
</p>

MIMRY is a command-line tool that tells AI coding agents which files, and which lines in them, to read for a task.
It parses a repository into symbols and a dependency graph, keeps the index on your machine, and ranks the files that matter for a task described in plain words.
The index is deterministic: the same code gives the same graph, rankings and context pack on Windows, Linux and macOS, and no LLM is involved in building it.
Agents call it from the shell or through its MCP server.

<p align="center">
  <img src="https://raw.githubusercontent.com/jbacalso24/mimry/main/assets/demo.gif" alt="mimry preflight in a terminal: it ranks the Flask files for a session cookie bug and names the lines to read first" width="100%">
</p>

## Installation

Get started in four steps.

1. Install uv, the tool that installs MIMRY.
   Pick the command for your system.
   macOS or Linux:

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

   Windows, in PowerShell:

   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

   Then open a new terminal.
   Already have uv? Skip this step.
   For other ways to install uv, see the [uv installation guide](https://docs.astral.sh/uv/getting-started/installation/).

2. Install MIMRY.

   ```bash
   uv tool install mimry
   ```

   MIMRY needs Python 3.11 or newer on Windows, Linux or macOS.
   uv downloads a suitable Python itself if none is installed.
   If the shell says `mimry` is not found, run `uv tool update-shell`, then open a new terminal.

3. Connect your agent.
   This is what makes the agent use MIMRY on its own.
   Claude Code users pick one of these two, not both:

   ```text
   /plugin marketplace add jbacalso24/mimry
   /plugin install mimry@mimry
   ```

   or run `mimry install --platform claude-code`.
   Other agents: run `mimry install --platform <name>`, see [Supported agents](#supported-agents).

   `mimry install --platform claude-code` also adds MIMRY's hooks, which steer the agent to MIMRY and do nothing in folders MIMRY has not set up.
   Add `--no-hooks` to leave them out.
   With `--project`, `--always-on` also adds MIMRY's rules to the agent's always-read file.

4. Check it works.
   In a repository, run `mimry preflight "<a task in your own words>"`.
   The first run builds the index and downloads the tree-sitter grammars it needs, once per user.
   If something looks off, run `mimry doctor`.

> [!NOTE]
> To upgrade, run `mimry upgrade`.
> On Windows, `mimry upgrade` refuses while MIMRY servers run, and `mimry upgrade --stop-servers` stops them first.

## Other ways to install

These replace steps 1 and 2, or add an optional feature.

- [pipx or a venv](docs/install.md#without-uv)
- [Docker](docs/install.md#docker)
- [Claude Code plugin](docs/install.md#claude-code-plugin)
- [npx skills](docs/install.md#other-agents-npx-skills), for other agents, skill only
- [Embeddings](docs/install.md#embeddings-optional), for search by meaning

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

Prompt hook: [Name the files for each task](docs/commands.md#name-the-files-for-each-task).
Token savings: [See the tokens MIMRY saved](docs/commands.md#see-the-tokens-mimry-saved).

## Features

- **Ranked lookup.** Ranks files and names the lines to read first in each.
- **Code graph.** Parses 41 languages with tree-sitter.
- **Deterministic.** Same code, same results on every machine ([how](docs/determinism.md)).
- **Documents.** Indexes Markdown, PDF, Word, Excel and PowerPoint.
- **Incremental.** Re-reads only the files that changed.
- **Feedback.** Ranks files that helped on similar tasks higher.
- **MCP server.** Exposes every lookup as an MCP tool.
- **Local.** No LLM, API key or telemetry.

## Supported languages

MIMRY parses these with tree-sitter.

| Area | File types | What MIMRY reads |
|---|---|---|
| Programming | `.py`, `.js`, `.ts`, `.go`, `.java` and more | Symbols, imports, calls |
| Stylesheets | `.css`, `.scss` | Selectors, variables, mixins |
| Markup | `.html`, `.vue`, `.svelte` | Element ids and components |
| SQL | `.sql` | Tables and the objects that use them |
| Data | `.json`, `.yaml`, `.toml`, `.xml`, `.tf` | Keys and names, never values |
| Build files | `.gradle`, `package.json`, `pyproject.toml` | Tasks, commands, entry points |
| Documents | `.md`, `.pdf`, `.docx`, `.xlsx`, `.pptx` | Headings, pages, sheets, slides |

Every extension is in [docs/languages.md](docs/languages.md).

## Supported agents

`mimry install --platform <name>` installs a skill that teaches the agent when to use MIMRY, and registers the MCP server where the agent supports it.

| Agent | Platform name | Skill | MCP server |
|---|---|---|---|
| Claude Code | `claude-code` | ✅ | ✅ |
| Codex | `codex` | ✅ | ✅ |
| GitHub Copilot CLI | `copilot` | ✅ | ✅ |
| GitHub Copilot in VS Code | `vscode` | ✅ | ✅ |
| Gemini CLI | `gemini` | ✅ | ✅ |
| OpenCode | `opencode` | ✅ | ✅ |
| Kiro | `kiro` | ✅ | ✅ |
| Cursor | `cursor` | ✅ | ✅ |
| Amp | `amp` | ✅ | ✅ |
| Devin | `devin` | ✅ | ✅ |
| Factory Droid | `droid` | ✅ | ✅ |
| Kimi | `kimi` | ✅ | ✅ |
| Kilo Code | `kilo` | ✅ | [manual](docs/agents.md#manual-mcp-setup) |
| Antigravity, CodeBuddy, Hermes, OpenClaw, Trae | see `mimry install --list-platforms` | ✅ | [manual](docs/agents.md#manual-mcp-setup) |
| Trae CN | `trae-cn` | ✅ | [manual](docs/agents.md#manual-mcp-setup) |
| Aider, Pi Agent | see `mimry install --list-platforms` | ✅ | none |
| Agent Skills | `agents` | ✅ | none |
| Windsurf | `windsurf` | ✅ | [manual](docs/agents.md#manual-mcp-setup) |
| Cline | `cline` | ✅ | [manual](docs/agents.md#manual-mcp-setup) |
| Roo Code | `roo` | ✅ | [manual](docs/agents.md#manual-mcp-setup) |
| Zed | `zed` | ✅ | ✅, or printed to paste in when its settings file has comments |
| JetBrains Junie | `junie` | ✅ | ✅ |
| Grok Build | `grok` | ✅ | not yet |

✅ means `mimry install` sets it up, and manual means you add the MCP server by hand.
none means the agent has no MCP support, so the skill alone covers it.
not yet means `mimry install` does not register an MCP server for the agent yet.

Each agent's files, the [install options](docs/agents.md#install-options) and [manual MCP setup](docs/agents.md#manual-mcp-setup) are in [docs/agents.md](docs/agents.md).

## Usage

```bash
mimry preflight "<task>"            # list the files and lines to read first, and write a context pack
mimry find "<query>"                # rank files for a query
mimry symbol <name>                 # find where a function, class or heading is defined
mimry callers <name>                # list the functions that call a function, from the graph
mimry callees <name>                # list the functions a function calls, from the graph
mimry related "<query>"             # list files connected through imports and calls
mimry explain "<task>"              # show the key files and symbols, and how they connect
mimry why <file> --query "<task>"   # explain why a file ranked
mimry feedback ...                  # record which files helped
mimry status                        # check whether the index is current
mimry doctor                        # diagnose the install and the index
```

See [docs/commands.md](docs/commands.md) for every command and option.
When something misbehaves, see [docs/troubleshooting.md](docs/troubleshooting.md).

## Configuration

A `mimry.toml` at the repository root controls which files are indexed.
Commit it so everyone on the team gets the same index.
The options and examples are in [docs/configuration.md](docs/configuration.md).

## Benchmarks

These numbers come from a scripted retrieval benchmark, not from complete agent sessions.
Measured with MIMRY 0.9.0 on 44 merged pull requests from 6 open-source projects, the set rerun for every release:

| How the agent reads files | Tokens saved with MIMRY | Tasks located, MIMRY / grep |
|---|---|---|
| The line ranges MIMRY names first | **71.4%** | **32** / 28 |
| Whole files | **51.0%** | **33** / 28 |
| `grep -n`, then 100 lines around each match | **48.6%** | **31** / 27 |

Method, SWE-bench results and a worked example: [docs/benchmarks.md](docs/benchmarks.md).

## How it works

MIMRY parses each file into symbols and edges, ranks files against a task, and re-reads only the files that changed.
The three steps are described in [docs/how-it-works.md](docs/how-it-works.md).

## Privacy

Indexing and search run locally.
Credential files are skipped.
The details, including how secrets are redacted, are in [docs/privacy.md](docs/privacy.md).

## Contributing

Bug reports and pull requests are welcome.
See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and checks.
MIMRY is in public alpha, and its interfaces may change before 1.0.

Thanks to everyone who has contributed:

[![MIMRY contributors](https://jbacalso24.github.io/mimry/contributors.svg)](https://jbacalso24.github.io/mimry/#contributors)

## License

[MIT](LICENSE)
