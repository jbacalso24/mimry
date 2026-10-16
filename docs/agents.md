# Agent setup

`mimry install --platform <name>` installs the MIMRY skill for an agent and registers the MCP server where the agent supports it.
Run `mimry install --list-platforms` for every platform name and alias.

```bash
mimry install --platform <name>             # install for every repository
mimry install --platform <name> --status    # check an install
mimry uninstall --platform <name>           # remove it
```

Add `--project` to any of these to work on the current repository only.
`--status` exits 0 when the install is up to date, 1 when the skill is not installed, and 2 when it needs repair.
It also reports a skill or always-on block written by an older MIMRY as out of date.

## How the MCP server is registered

| Agent | Registration |
|---|---|
| Claude Code, Codex, Gemini CLI, GitHub Copilot CLI, Factory Droid, Kimi, Amp, Devin | Through the agent's own CLI, at user scope |
| Cursor, Kiro, OpenCode, VS Code, JetBrains Junie | In the agent's config file |
| Zed | In its `settings.json` under `context_servers`, or printed when that file has comments |
| Aider, Pi Agent, Agent Skills | None; the agent has no MCP support, so the skill alone covers it |
| Grok Build | None yet; its docs do not confirm an MCP config file |
| Any other agent, including Kilo Code, Windsurf, Cline and Roo Code | `mimry install` prints the snippet to add to its MCP settings |

Pass `--no-mcp`, or set `MIMRY_NO_MCP_REGISTRATION=1`, to install the skill without registering the server.
A `--no-mcp` install remembers that choice, so `--status` shows the MCP server as `not registered (--no-mcp)` and does not report the install as needing repair.
A later `mimry install` without `--no-mcp` registers the server and clears the choice.
The agent starts its own `mimry-mcp` when a session begins and stops it when the session ends.

`mimry install` also adds `.mimry/` to Git's global ignore file, once per machine.
That file is `core.excludesFile` when it is set, otherwise `$XDG_CONFIG_HOME/git/ignore` or `~/.config/git/ignore`.
Pass `--no-global-gitignore` to leave it alone.
`mimry uninstall --git-ignore` removes those lines again.

## OpenCode

Install MIMRY for OpenCode with:

```bash
mimry install --platform opencode
```

This installs the MIMRY skill at `~/.config/opencode/skills/mimry/SKILL.md` and adds the local MCP server to `~/.config/opencode/opencode.json`.
Check the result with:

```bash
mimry install --platform opencode --status
```

## Kilo Code

Install the MIMRY skill for Kilo Code with:

```bash
mimry install --platform kilo
```

The command writes the skill to `~/.config/kilo/skills/mimry/SKILL.md`; it does not modify a Kilo MCP configuration.
It prints the MCP snippet for you to add to Kilo's settings by hand.
Check the skill installation with:

```bash
mimry install --platform kilo --status
```

## Cursor

Install MIMRY for Cursor with:

```bash
mimry install --platform cursor
```

This installs the MIMRY skill at `~/.cursor/skills/mimry/SKILL.md` and adds the local MCP server to `~/.cursor/mcp.json`.
Add `--project` to install the skill at `.cursor/skills/mimry/` in the current repository instead.
Add `--always-on` with `--project` to write the MIMRY section to `AGENTS.md`, which Cursor reads.
Check the result with:

```bash
mimry install --platform cursor --status
```

## GitHub Copilot in VS Code

Install MIMRY for GitHub Copilot in VS Code with:

```bash
mimry install --platform vscode
```

This installs the MIMRY skill at `~/.copilot/skills/mimry/SKILL.md` and adds the local MCP server to the `servers` section of your VS Code user `mcp.json`.
That file is at `%APPDATA%\Code\User\mcp.json` on Windows, `~/Library/Application Support/Code/User/mcp.json` on macOS, and `$XDG_CONFIG_HOME/Code/User/mcp.json` (default `~/.config/Code/User/mcp.json`) on Linux.
The skill path is shared with GitHub Copilot CLI, so uninstalling either one removes the skill for both.
Add `--project` to install the skill at `.github/skills/mimry/` in the current repository instead.
Add `--always-on` with `--project` to write the MIMRY section to `.github/copilot-instructions.md`, which Copilot reads.
Check the result with:

```bash
mimry install --platform vscode --status
```

## Windsurf

Install MIMRY for Windsurf with:

```bash
mimry install --platform windsurf
```

This installs the MIMRY skill at `~/.codeium/windsurf/skills/mimry/SKILL.md`.
Add `--project` to install the skill at `.windsurf/skills/mimry/` in the current repository instead.
Add `--always-on` with `--project` to write the MIMRY section to `AGENTS.md`, which Windsurf reads.
Windsurf's docs do not confirm where its MCP server config lives, so `mimry install` prints the MCP snippet for you to add by hand.
Check the result with:

```bash
mimry install --platform windsurf --status
```

## Cline

Install MIMRY for Cline with:

```bash
mimry install --platform cline
```

This installs the MIMRY skill at `~/.cline/skills/mimry/SKILL.md`.
Add `--project` to install the skill at `.cline/skills/mimry/` in the current repository instead.
Add `--always-on` with `--project` to write the MIMRY section to `AGENTS.md`, which Cline reads.
Cline loads skills only after you turn on Enable Skills under Settings, then Features.
Cline's MCP settings file is per extension and is not in its docs, so `mimry install` prints the MCP snippet for you to add by hand.
Check the result with:

```bash
mimry install --platform cline --status
```

## Roo Code

Install MIMRY for Roo Code with:

```bash
mimry install --platform roo
```

This installs the MIMRY skill at `~/.roo/skills/mimry/SKILL.md`.
Add `--project` to install the skill at `.roo/skills/mimry/` in the current repository instead.
Add `--always-on` with `--project` to write the MIMRY section to `AGENTS.md`, which Roo Code reads.
Roo Code's global MCP settings file is not documented with a path, so `mimry install` prints the MCP snippet for you to add by hand.
Check the result with:

```bash
mimry install --platform roo --status
```

## Zed

Install MIMRY for Zed with:

```bash
mimry install --platform zed
```

This installs the MIMRY skill at `~/.agents/skills/mimry/SKILL.md`.
Add `--project` to install the skill at `.agents/skills/mimry/` in the current repository instead.
The `.agents` folder is shared with `mimry install --platform agents`, so each install rewrites the same file and the last one wins.
Add `--always-on` with `--project` to write the MIMRY section to `AGENTS.md`, which Zed reads.
`mimry install` adds the local MCP server under `context_servers` in Zed's `settings.json`: `~/.config/zed/` on macOS, `$XDG_CONFIG_HOME/zed/` (default `~/.config/zed/`) on Linux, and `%APPDATA%\Zed\` on Windows.
Zed's settings file usually starts with comments, and MIMRY never rewrites a file it cannot parse as plain JSON.
In that case `mimry install` prints the `context_servers` entry for you to paste into Zed's settings.
Check the result with:

```bash
mimry install --platform zed --status
```

## JetBrains Junie

Install MIMRY for JetBrains Junie with:

```bash
mimry install --platform junie
```

This installs the MIMRY skill at `~/.junie/skills/mimry/SKILL.md` and adds the local MCP server to the `mcpServers` section of `~/.junie/mcp/mcp.json`, the Junie CLI's user config.
Add `--project` to install the skill at `.junie/skills/mimry/` in the current repository instead.
Add `--always-on` with `--project` to write the MIMRY section to `AGENTS.md`, which Junie reads.
The JetBrains IDE plugin keeps its MCP settings in the IDE, and `mimry install` does not write them.
Check the result with:

```bash
mimry install --platform junie --status
```

## Grok Build

Install MIMRY for Grok Build with:

```bash
mimry install --platform grok
```

This installs the MIMRY skill at `~/.grok/skills/mimry/SKILL.md`.
Add `--project` to install the skill at `.grok/skills/mimry/` in the current repository instead.
Grok Build's docs do not confirm that it reads `AGENTS.md` or an MCP config file, so `--always-on` does nothing and no MCP server is registered.
Check the result with:

```bash
mimry install --platform grok --status
```
