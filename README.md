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
  <a href="#benchmarks">On 92 real pull requests, agents read 50% fewer tokens with MIMRY.</a>
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

### Without uv

uv is the recommended way to install MIMRY, but it is not required.
MIMRY needs Python 3.11 or newer in an environment of its own, and these options all provide one.

- **pipx:** install pipx with `sudo pacman -S python-pipx` on Arch or `sudo apt install pipx` on Debian and Ubuntu, then run `pipx install mimry`.
- **A plain virtual environment:** run `python -m venv ~/.local/share/mimry-venv` and then `~/.local/share/mimry-venv/bin/pip install mimry`, then put the command on your PATH with `ln -s ~/.local/share/mimry-venv/bin/mimry ~/.local/bin/mimry`.

On distros that block system pip, a plain `pip install mimry` fails with `externally-managed-environment`.
These include Arch, CachyOS, Debian 12 and later, and Ubuntu 23.04 and later, so one of the options above is needed there.
Do not use `--break-system-packages`.

On Arch, `sudo pacman -S uv` also works.
An AUR package is coming, see [#64](https://github.com/jbacalso24/mimry/issues/64).

`mimry upgrade` uses the same tool that installed MIMRY, so run it the same way.

### Docker

The image `ghcr.io/jbacalso24/mimry` has every tree-sitter grammar built in, so indexing needs no network.
Tags are `latest`, the full version such as `0.5.0`, and the minor line such as `0.5`.

Mount your repo at `/repo` and keep the index in a named volume at `/cache`.
Without the volume, each container starts with no index.
On Linux, add `--user "$(id -u):$(id -g)"` so the `.mimry/` folder MIMRY writes in your repo belongs to you.

```bash
docker run --rm -v "$PWD:/repo" -v mimry-cache:/cache ghcr.io/jbacalso24/mimry init
docker run --rm -v "$PWD:/repo" -v mimry-cache:/cache ghcr.io/jbacalso24/mimry index
docker run --rm -v "$PWD:/repo" -v mimry-cache:/cache ghcr.io/jbacalso24/mimry find "<query>"
```

For an agent, run the MCP server over stdio with the same mounts:

```bash
docker run -i --rm -v "$PWD:/repo" -v mimry-cache:/cache ghcr.io/jbacalso24/mimry mcp
```

In an MCP client config, use the repo's absolute path, because `$PWD` is not expanded there:

```json
{
  "mcpServers": {
    "mimry": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "-v", "/absolute/path/to/repo:/repo", "-v", "mimry-cache:/cache", "ghcr.io/jbacalso24/mimry", "mcp"]
    }
  }
}
```

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
| Code graph | Maps imports, calls and inheritance across 41 languages with tree-sitter |
| Deterministic | The same code gives the same symbols, graph, rankings and context pack on every machine; CI checks every change against committed golden values ([how](docs/determinism.md)) |
| Documents | Indexes Markdown, text, PDF, Word, Excel and PowerPoint files alongside code |
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
| Groovy | `.gradle`, `.groovy`, `.gvy` |
| Swift | `.swift` |
| Bash | `.bash`, `.sh` |
| PowerShell | `.ps1`, `.psm1` |
| Lua | `.lua` |
| Perl | `.pl`, `.pm` |
| Dart | `.dart` |
| Objective-C | `.m` |
| Zig | `.zig` |
| Elixir | `.ex`, `.exs` |
| Haskell | `.hs` |
| OCaml | `.ml`, `.mli` |
| R | `.r` |
| Julia | `.jl` |
| Solidity | `.sol` |
| Fortran | `.f03`, `.f08`, `.f90`, `.f95` |
| Pascal | `.dpr`, `.lpr`, `.pas`, `.pp` |
| CSS | `.css` |
| SCSS | `.scss` |
| HTML | `.htm`, `.html` |
| Vue | `.vue` |
| Svelte | `.svelte` |
| SQL | `.sql` |
| JSON | `.json`, `.jsonc` |
| YAML | `.yaml`, `.yml` |
| TOML | `.toml` |
| XML | `.csproj`, `.fsproj`, `.nuspec`, `.plist`, `.props`, `.resx`, `.targets`, `.vbproj`, `.xaml`, `.xml`, `.xsd` |
| Terraform (HCL) | `.hcl`, `.tf`, `.tfvars` |

Fortran is read in free form only. Fixed-form `.f` and `.for` files do not parse with the grammar MIMRY uses, so they get no symbols or edges.

`.pp` and `.m` are checked first, because most `.pp` files are Puppet and most `.m` files are MATLAB.
A `.pp` file is Pascal only when it has a `unit`, `program` or `library` line, and a `.m` file is Objective-C only when it has an `@interface`, `@implementation`, `@protocol` or `#import` line.
Any other `.pp` or `.m` file is indexed as searchable text with no symbols.

The markup and style languages get what an agent searches them by.
CSS and SCSS give their class and id selectors, custom properties, keyframes, Sass mixins, functions, placeholders and variables, with `@import`, `@use` and `@forward` as imports.
HTML gives each element id and the local stylesheets and scripts it loads.
Vue and Svelte give each component (named after its file) and run the JavaScript or TypeScript and CSS or SCSS passes over their `<script>` and `<style>` blocks, so their line numbers match the file.

MIMRY also indexes Markdown (headings, links, frontmatter), plain text, PDF, Word, Excel and PowerPoint files. Results name the page, section, sheet or slide that matches.

Some files get more structure than their text:

- SQL: `.sql` files give their schema objects and the tables they touch, see below. Python string literals keep their `CREATE TABLE` names, and Alembic migrations keep their revision lineage.
- Groovy and Gradle: `.groovy`, `.gradle` and `.gvy` files give their classes, interfaces, traits, enums, methods and functions, and the `task` entries of a Gradle build, see below.
- Manifests: `package.json`, `pyproject.toml`, `tsconfig.json`, `vite.config.*`, `next.config.*`, `app.json`, `app.config.*`, `.env.example`, `CLAUDE.md`, `AGENTS.md` and `README.md` give their commands, frameworks, entry points and env variable names, never values.

The data formats get the names an agent looks them up by.
JSON, YAML and TOML give their keys to depth two, named by path, such as `services.web` or `tool.ruff.line-length`, and TOML also gives each table header.
A YAML document that is a Kubernetes object also gives a `<kind>/<name>` resource, such as `Deployment/web`.
XML gives each element with an `id` or `name` attribute, and a Maven `pom.xml` gives its `groupId:artifactId`.
MSBuild project and props files give each property in a `PropertyGroup` and each `Target` name, with each `ProjectReference` as an import, a plist gives the keys of its top-level dict, and XAML gives each `x:Name` and `x:Key`.
Terraform gives each `resource`, `data`, `module`, `variable`, `output`, `provider` and `locals` entry under the name it is referenced by, such as `aws_s3_bucket.b`, `data.aws_iam_policy_document.p`, `module.bucket`, `var.region` and `local.name`.
A `var.region` reference finds the variable in the same folder, and a module's local `source` links to its folder.
Values are never symbols, so a secret stored as a value is never indexed.
Lock files and files over 256 KiB get no symbols, and manifests keep their own adapter above.
Every data file is also indexed as text, so `mimry find` matches its content.

SQL files name each schema object as the source writes it, such as `public.users`: tables, views, materialized views, indexes, sequences, schemas and types, and functions, procedures and triggers.
A view, function, trigger or index that reads or writes a table gets a call edge to it, and so does a foreign key's `REFERENCES` target.
A plain query or an `ALTER TABLE` belongs to no object, so it adds no edge.
A statement the parser does not know costs only itself, and the objects around it are kept.
A trigger written with a `BEGIN ... END` body gives its name and table, but its body is not read.
Files over 256 KiB get no symbols, so a large dump stays text-searchable.

Groovy and Gradle files name each class, interface, trait, enum, method and top-level `def` function as the source writes it.
A Spock feature method keeps its string, such as `adds two numbers`, as its name.
A Gradle `task name` or `tasks.register("name")` gives a `task`, and `plugins {}` and `dependencies {}` entries are not symbols.
An `import` of a Groovy class links exactly to the file that declares it, and a wildcard import such as `a.b.*` links to every file of package `a.b`.
A static wildcard such as `import static a.b.C.*` links to the file of class `C`.
Calls inside a method body, and the `extends` and `implements` clauses of a type, give call and inheritance edges, and a paren-less `name arg` call counts only for a method of the same file.
A piece of a file the parser cannot read costs only itself, so an annotated type such as `@Service(Book)` keeps its methods.
Closure-assigned fields and operator methods such as `<<` are not symbols.

Other extensions can be mapped to a supported language in `mimry.toml`, see [Configuration](#configuration).

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
| Trae CN | `trae-cn` | ✅ | [manual](#manual-mcp-setup) |
| Aider, Pi Agent | see `mimry install --list-platforms` | ✅ | none |
| Agent Skills | `agents` | ✅ | none |
| Windsurf | `windsurf` | ✅ | [manual](#manual-mcp-setup) |
| Cline | `cline` | ✅ | [manual](#manual-mcp-setup) |
| Roo Code | `roo` | ✅ | [manual](#manual-mcp-setup) |
| Zed | `zed` | ✅ | ✅, or printed to paste in when its settings file has comments |
| JetBrains Junie | `junie` | ✅ | ✅ |
| Grok Build | `grok` | ✅ | not yet |

✅ means `mimry install` sets it up, and manual means you add the MCP server by hand.
none means the agent has no MCP support, so the skill alone covers it.
not yet means `mimry install` does not register an MCP server for the agent yet.

See [docs/agents.md](docs/agents.md) for where each agent's files go and how its MCP server is registered.

| Option | Description |
|---|---|
| `--project` | Install into the current repository only, instead of for every repository |
| `--always-on` | Add a short rules block to the agent's always-read file, such as `AGENTS.md` |
| `--hooks` | Suggest `mimry find` when the agent starts a broad grep, answer a search for a known symbol with its definition and callers, and refresh the index in the background when a session starts and after each file edit. For Codex, project hooks run after you trust them with `/hooks`. |
| `--no-mcp` | Install the skill without registering the MCP server |
| `--no-global-gitignore` | Do not add `.mimry/` to Git's global ignore file |

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

By default the server lists 9 core tools: `mimry_status`, `mimry_preflight`, `mimry_find`, `mimry_related`, `mimry_symbol`, `mimry_path`, `mimry_impact`, `mimry_why` and `mimry_feedback`.
The other tools stay off by default to keep the tool list small, so use the CLI for them unless you ask for them.
Set `MIMRY_MCP_TOOLS=all` in the server's environment to list all 24 tools, or give a comma-separated list of tool names, such as `MIMRY_MCP_TOOLS=mimry_find,mimry_path`.
Commands with no tool, such as `mimry callers`, `mimry callees`, `mimry bench`, `mimry doctor`, `mimry report` and `mimry install`, stay CLI-only.
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
When something misbehaves, see [docs/troubleshooting.md](docs/troubleshooting.md).

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

MIMRY always skips folders named `build`, `bin`, `obj`, `dist`, `target`, `node_modules` and `vendor`, at any depth, along with caches and virtual environments.
`include` does not bring them back, so MSBuild project and props files in `build/`, such as `build/Common.props`, get no symbols.

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

To measure MIMRY on your own repository's merged pull requests, run `mimry bench`; see [Benchmark your repository](docs/commands.md#benchmark-your-repository).

### One task, start to finish

Task `fastapi/typer#1820` from the holdout2 run: "Respect wait=False when launching URLs with xdg-open".
The fix changed `typer/main.py`.
Its 75.3% saving is the median for that run, so it is a typical task, not the best one.

**Without MIMRY**

The agent runs seven keyword searches for `respect`, `wait`, `false`, `launching`, `urls`, `xdg` and `open`, which cost 1,927 tokens.
It then reads `typer/main.py` from the top, up to the benchmark's 2,000-line limit per read, which costs 13,491 tokens.
Total: 15,418 tokens.

**With MIMRY**

`mimry preflight "Respect wait=False when launching URLs with xdg-open"` costs 1,876 tokens.
The agent then reads six line ranges in the order of the context pack, which cost 1,937 tokens:

| File | Lines read | Tokens |
|---|---|---|
| `tests/test_launch.py` | 44-52, 119-134 | 184 |
| `docs_src/launch/tutorial001_py310.py` | 6-9 | 28 |
| `docs_src/launch/tutorial002_py310.py` | 10-20 | 105 |
| `typer/_click/utils.py` | 111-136, 333-368 | 536 |
| `typer/_click/_termui_impl.py` | 306-309, 345-414 | 476 |
| `typer/main.py` (the file the fix changed) | 1474-1482, 1932-2009 | 608 |

Total: 3,813 tokens.

MIMRY opened six files instead of one and still read 75.3% fewer tokens: 3,813 against 15,418.

The numbers come from [`results.holdout2.ranges.0.4.1.json`](benchmarks/tokens/results.holdout2.ranges.0.4.1.json), measured with MIMRY 0.4.1 in token benchmark run 38884765315.
To reproduce them, see [Running it](benchmarks/tokens/README.md#running-it).

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
