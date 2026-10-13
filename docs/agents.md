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
| Cursor, Kiro, OpenCode, VS Code | In the agent's config file |
| Kilo Code | Not registered; the skill only |
| Any other agent | `mimry install` prints the snippet to add to its MCP settings |

Pass `--no-mcp`, or set `MIMRY_NO_MCP_REGISTRATION=1`, to install the skill without registering the server.
The agent starts its own `mimry-mcp` when a session begins and stops it when the session ends.

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
