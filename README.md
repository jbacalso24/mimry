<p align="center">
  <img src="https://raw.githubusercontent.com/jbacalso24/mimry/main/assets/banner.png" alt="MIMRY - The local context engine for AI coding agents" width="100%">
</p>

<h1 align="center">MIMRY</h1>

<!-- mcp-name: io.github.jbacalso24/mimry -->

<p align="center">
  The local context engine for AI coding agents.
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

```bash
uv tool install mimry
```

Requires Python 3.11 or newer on Windows, Linux or macOS.
`pipx install mimry` and `pip install mimry` also work.

The first index downloads the tree-sitter grammars it needs, once per user, and keeps them in a cache.

> [!NOTE]
> To upgrade, run `mimry upgrade`.
> On Windows, `mimry upgrade` refuses while MIMRY servers run, and `mimry upgrade --stop-servers` stops them first.

### Claude Code plugin

In Claude Code, the plugin gives the agent the MIMRY skill, the hooks and the MCP server in one step.
It needs `uv` on your PATH and nothing else.

```text
/plugin marketplace add jbacalso24/mimry
/plugin install mimry@mimry
```

It replaces `mimry install --platform claude-code`, so use one or the other.

### Other agents: npx skills

It installs the skill only; for the MCP server and hooks, run `mimry install --platform <name>` (see [Supported agents](#supported-agents)).

```text
npx skills add jbacalso24/mimry
```

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
> Add `--always-on --hooks` so the agent reaches for MIMRY without being told: `--always-on` adds the rules to its always-read file, and `--hooks` suggests `mimry find` whenever it starts a broad grep, and answers a grep for a known symbol with its definition and callers. The hooks also refresh the index in the background when a session starts and after each file edit.

## Features

| Feature | Description |
|---|---|
| Ranked lookup | Ranks files by name, path, content, symbol and graph matches, and shows why each one ranked |
| Line ranges | Names the functions, classes or sections to read first in each file |
| Code graph | Maps imports, calls and inheritance across 21 languages with tree-sitter |
| Deterministic | The same code gives the same symbols, graph, rankings and context pack on every machine; CI checks every change against committed golden values ([how](docs/determinism.md)) |
| Documents | Indexes Markdown, text, PDF, Word and Excel files alongside code |
| Incremental indexing | Re-reads only changed files, and refreshes the index before the main lookups answer |
| Feedback | Ranks files recorded as useful higher for similar tasks |
| MCP server | Exposes every lookup as an MCP tool |
| Local | Runs without an LLM, API key or telemetry, and keeps your code on your machine |

## Supported languages

MIMRY parses these languages with tree-sitter for symbols, imports, calls and inheritance.

| Language | Extensions |
|---|---|
| Python | `.py` |
| JavaScript | `.cjs`, `.js`, `.jsx`, `.mjs` |
| TypeScript | `.cts`, `.mts`, `.ts`, `.tsx` |
| Go | `.go` |
| Rust | `.rs` |
| C# | `.cs` |
| Java | `.java` |
| PHP | `.php` |
| C | `.c`, `.h` |
| C++ | `.cc`, `.cpp`, `.cxx`, `.hh`, `.hpp`, `.hxx` |
| Ruby | `.rb` |
| Kotlin | `.kt`, `.kts` |
| Scala | `.sc`, `.scala` |
| Swift | `.swift` |
| Bash | `.bash`, `.sh` |
| PowerShell | `.ps1`, `.psm1` |
| Lua | `.lua` |
| Perl | `.pl`, `.pm` |
| Dart | `.dart` |
| Objective-C | `.m` |
| Zig | `.zig` |

MIMRY also indexes Markdown (headings, links, frontmatter), plain text, PDF, Word and Excel files.

Other extensions can be mapped to a supported language in `mimry.toml`, see [Configuration](#configuration).

### Planned

We're adding these next; progress is tracked in [#47](https://github.com/jbacalso24/mimry/issues/47), where you can ask for yours.

- [ ] Web and markup: HTML, CSS, SCSS, Vue, Svelte
- [ ] Data and config: SQL, JSON, YAML, TOML, XML, Terraform (HCL)
- [ ] Apps and systems: Groovy
- [ ] Functional and scientific: Elixir, Haskell, OCaml, R, Julia
- [ ] Other: Solidity, Fortran, Pascal

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
| Kilo Code | `kilo` | ✅ | [manual](#manual-mcp-setup) |
| Antigravity, CodeBuddy, Hermes, OpenClaw, Trae | see `mimry install --list-platforms` | ✅ | [manual](#manual-mcp-setup) |
| Aider, Pi Agent | see `mimry install --list-platforms` | ✅ | none |
| Windsurf, Cline, Roo Code, Zed, JetBrains | [#80](https://github.com/jbacalso24/mimry/issues/80) | 🚧 | 🚧 |
| Grok | [#93](https://github.com/jbacalso24/mimry/issues/93) | 🚧 | 🚧 |

✅ means `mimry install` sets it up, manual means you add the MCP server by hand, and 🚧 means support is in progress.
none means the agent has no MCP support, so the skill alone covers it.

See [docs/agents.md](docs/agents.md) for where each agent's files go and how its MCP server is registered.

| Option | Description |
|---|---|
| `--project` | Install into the current repository only, instead of for every repository |
| `--always-on` | Add a short rules block to the agent's always-read file, such as `AGENTS.md` |
| `--hooks` | Suggest `mimry find` when the agent starts a broad grep, answer a search for a known symbol with its definition and callers, and refresh the index in the background when a session starts and after each file edit. For Codex, project hooks run after you trust them with `/hooks`. |
| `--no-mcp` | Install the skill without registering the MCP server |
| `--no-global-gitignore` | Do not add `.mimry/` and `mimry-out/` to Git's global ignore file |

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

By default the server lists 8 core tools: `mimry_status`, `mimry_preflight`, `mimry_find`, `mimry_related`, `mimry_symbol`, `mimry_path`, `mimry_why` and `mimry_feedback`.
Their definitions cost 1,309 tokens per session (o200k_base), against 2,590 for all 22 tools, so the rest stay CLI commands unless you ask for them.
Set `MIMRY_MCP_TOOLS=all` in the server's environment to list every command as a tool, or give a comma-separated list of tool names, such as `MIMRY_MCP_TOOLS=mimry_find,mimry_path`.
An unknown name prints one line to stderr and falls back to the core tools.

MIMRY is listed in the [MCP Registry](https://registry.modelcontextprotocol.io/) as `io.github.jbacalso24/mimry`.

</details>

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

## Configuration

A `mimry.toml` at the repository root controls which files are indexed.
Commit it so everyone on the team gets the same index.

```toml
[index]
include = ["src/**", "docs/**"]            # index only these paths
exclude = ["**/fixtures/**", "legacy/**"]  # never index these paths
max_file_bytes = 2_000_000                 # size limit per file (default 1 MB)

[index.languages]
".inc" = "php"                             # parse .inc files as PHP
".h" = "cpp"                               # parse .h headers as C++ instead of C
```

## Benchmarks

Tokens an agent read before it found the code each pull request changed, measured on 92 merged pull requests from 12 open-source projects.

| How the agent reads files | Tokens saved with MIMRY |
|---|---|
| Whole files | **50%** |
| `grep -n`, then a few lines around each match | **48%** |
| The line ranges MIMRY names first | **67%** |

See [benchmarks/tokens](benchmarks/tokens/README.md) for the method, per-project results and how to reproduce them.

On the 500 issues of SWE-bench Verified, one `mimry find` with the issue text puts every file the fix changes in the top 10 for 74.6% of issues, in the top 5 for 63.8%, and first for 34.8%.
See [benchmarks/localization](benchmarks/localization/README.md) for the protocol and per-repository results.

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

Thanks to everyone who has contributed:

[![MIMRY contributors](https://jbacalso24.github.io/mimry/contributors.svg)](https://jbacalso24.github.io/mimry/#contributors)

## License

[MIT](LICENSE)
